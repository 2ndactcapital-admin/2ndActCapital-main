"""aibudget1.structural — daily model budgets, enforced at the call.

WHAT A BUDGET IS
──────────────────────────────────────────────────────────────────────────────
When the super admin assigns Model 1 and Model 2 to an ensemble job
(``ai_ensemble_configs``), each model gets a budget, stored on the same
immutable config row:

  * ``unlimited``       — no check (the per-run dollar cap still applies when a
                          run sets one; services.note_extraction.spend)
  * ``tokens_per_day``  — a whole number of tokens (input + output)
  * ``usd_per_day``     — dollars (Decimal, cents)

The number is whatever the super admin types from the provider's dashboard
(OpenAI's complimentary daily tokens for traffic shared with OpenAI are not
published and have moved; the code never assumes them).

THE SHARED ALLOWANCE RULE
──────────────────────────────────────────────────────────────────────────────
A provider's daily allowance belongs to the MODEL, not to a job. Usage is
therefore counted per deployment per budget day across EVERY job that calls it
(``public.ai_daily_usage``), and a job's budget for a model is checked against
that shared total: "this job stops for the day when <model> has used <budget>
today across Hollisworks". Job A's usage counts against job B's budget on the
same model.

THE DAY BOUNDARY
──────────────────────────────────────────────────────────────────────────────
Midnight UTC. ``budget_day`` is computed by the DATABASE at the call —
``(now() AT TIME ZONE 'UTC')::date`` inside the reserve's own transaction —
never at module import and never from the host clock. OpenAI's daily
allowances reset on UTC days.

RESERVE FIRST, RECORD AFTER
──────────────────────────────────────────────────────────────────────────────
Every call made inside a :class:`BudgetScope` goes through
``services.note_extraction.proxy.chat`` — the one HTTP chokepoint for ensemble
calls — which asks the scope for a reservation BEFORE the request:

  estimate tokens = prompt characters / 4 (rounded up) + the call's max_tokens
  estimate dollars = the same tokens priced from the deployment's price

The reservation is ONE atomic statement per model (``UPDATE … WHERE used +
reserved + estimate <= budget RETURNING``) under the row lock, so two runs at
once cannot both pass the check: the second re-evaluates the WHERE after the
first commits. A reservation that would cross a limited budget is refused:
:class:`BudgetReached` (model, kind, budget, used) is raised and the call is
NOT made. After the call the reservation is replaced by the provider-reported
usage (``usage.prompt_tokens`` / ``completion_tokens``, the proxy's cost
header or the manual price); a call that came back with no usage keeps the
reservation as its recorded usage (pessimistic).

Output is bounded by max_tokens. Input is an ESTIMATE (chars / 4): a prompt
that tokenizes denser than that can record slightly more input than it
reserved. The next reservation sees the true total. A call whose body carries
no max_tokens (held_away's statement reader) gets ``AI_BUDGET_DEFAULT_MAX_TOKENS``
injected when its model has a LIMITED budget, so the reservation is a real
ceiling; with an unlimited budget the body is left exactly as it was.

NEVER A ONE-MODEL READING
──────────────────────────────────────────────────────────────────────────────
An ensemble compares two readings. :func:`pair` reserves BOTH models' calls in
ONE transaction before either is made — all or nothing. When either model is
out of budget, neither call is made and the job stops. Each model's call then
consumes its pre-made reservation instead of reserving again; anything not
consumed (a reader that refused before calling) is released when the pair
exits.
"""
from __future__ import annotations

import asyncio
import contextvars
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

from services.note_extraction.spend import SpendCapReached

UNLIMITED = "unlimited"
TOKENS_PER_DAY = "tokens_per_day"
USD_PER_DAY = "usd_per_day"
BUDGET_KINDS = (UNLIMITED, TOKENS_PER_DAY, USD_PER_DAY)
LIMITED_KINDS = (TOKENS_PER_DAY, USD_PER_DAY)

# The picker's vocabulary — served in the API envelope, never hard-coded in the UI.
BUDGET_KIND_OPTIONS = (
    {"key": UNLIMITED, "label": "Unlimited", "unit": None, "input": None},
    {"key": TOKENS_PER_DAY, "label": "Tokens per day", "unit": "tokens", "input": "whole_number"},
    {"key": USD_PER_DAY, "label": "Dollars per day", "unit": "USD", "input": "dollars"},
)
DAY_BOUNDARY = {
    "timezone": "UTC",
    "label": "Each budget day starts at midnight UTC",
    "source": "OpenAI's daily allowances reset on UTC days; the day is taken from the database clock at each call",
}
SEMANTICS_TEMPLATE = "This job stops for the day when {model} has used {budget} today across Hollisworks."
UNLIMITED_TEMPLATE = ("No daily limit on {model} for this job. Its usage is still counted across "
                      "Hollisworks, against any other job's budget for the same model.")
SHARED_RULE = ("Usage is counted per model across every job that calls it, because the provider's daily "
               "allowance belongs to the model, not to a job.")

BUDGET_DAY_SQL = "(now() AT TIME ZONE 'UTC')::date"
DEFAULT_MAX_TOKENS_ENV = "AI_BUDGET_DEFAULT_MAX_TOKENS"
DEFAULT_MAX_TOKENS = 16000
USD_Q = Decimal("0.00000001")


def default_max_tokens() -> int:
    raw = (os.environ.get(DEFAULT_MAX_TOKENS_ENV) or "").strip()
    try:
        v = int(raw) if raw else DEFAULT_MAX_TOKENS
    except ValueError:
        v = DEFAULT_MAX_TOKENS
    return v if v > 0 else DEFAULT_MAX_TOKENS


def utc_today():
    """The budget day by this host's clock — for DISPLAY only. Enforcement uses
    the database's clock inside the reserve (BUDGET_DAY_SQL)."""
    return datetime.now(timezone.utc).date()


def usd(v) -> Decimal:
    if v is None:
        return Decimal(0)
    return Decimal(str(v)).quantize(USD_Q, rounding=ROUND_HALF_UP)


def estimate_tokens(prompt_chars: int, max_tokens: int) -> int:
    return (int(prompt_chars) + 3) // 4 + int(max_tokens)


def prompt_chars_of(messages) -> int:
    n = 0
    for m in messages or []:
        c = m.get("content") if isinstance(m, dict) else None
        if isinstance(c, str):
            n += len(c)
        elif isinstance(c, list):
            n += sum(len(p.get("text") or "") for p in c if isinstance(p, dict))
    return n


def body_max_tokens(body: dict) -> int | None:
    for k in ("max_tokens", "max_completion_tokens"):
        v = body.get(k)
        if isinstance(v, int) and v > 0:
            return v
    return None


# ── errors ──────────────────────────────────────────────────────────────────
class BudgetReached(SpendCapReached):
    """A limited daily budget would be crossed by the next call; the call was
    not made. Subclasses SpendCapReached so every existing clean-stop path
    (run_notes, cascade._read_pair) records the calls already made and stops."""

    def __init__(self, deployment: str, kind: str, budget: Decimal, used: Decimal, needed: Decimal,
                 task_key: str | None = None, budget_day=None):
        unit = "tokens" if kind == TOKENS_PER_DAY else "USD"
        RuntimeError.__init__(
            self, f"daily budget reached for {deployment}: {used} of {budget} {unit} used today (UTC day "
                  f"{budget_day}) across Hollisworks; the next call needs {needed} {unit}")
        self.deployment, self.kind, self.budget, self.used, self.needed = deployment, kind, budget, used, needed
        self.task_key, self.budget_day = task_key, budget_day
        # SpendCapReached's attributes, so generic handlers can read them too.
        self.cap, self.spent, self.what = budget, used, deployment

    def as_dict(self) -> dict:
        return {"model": self.deployment, "kind": self.kind, "budget": str(self.budget), "used": str(self.used),
                "needed": str(self.needed), "budget_day": str(self.budget_day) if self.budget_day else None}


class BudgetConfigError(RuntimeError):
    """A budget cannot be enforced as configured (e.g. dollars on an unpriced model)."""


class LedgerWriteError(RuntimeError):
    """An UPDATE of ai_daily_usage matched zero rows."""


# ── limits ──────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Limit:
    kind: str
    value: Decimal | None

    @property
    def limited(self) -> bool:
        return self.kind in LIMITED_KINDS and self.value is not None


def limits_from_config(cfg: dict | None) -> dict[str, Limit]:
    """{deployment: Limit} from an ai_ensemble_configs row (Model 1 and Model 2)."""
    if not cfg:
        return {}
    out = {}
    for slot in ("model_1", "model_2"):
        dep = cfg.get(slot)
        if not dep:
            continue
        kind = cfg.get(f"{slot}_budget_kind") or UNLIMITED
        val = cfg.get(f"{slot}_budget_value")
        out[dep] = Limit(kind, Decimal(str(val)) if val is not None else None)
    return out


@dataclass
class Reservation:
    deployment: str
    budget_day: object
    tokens: int
    usd: Decimal
    done: bool = False


@asynccontextmanager
async def _platform_tx(pool):
    """One committed unit of work with app.is_super_admin LOCAL — on the RLS
    pool (acquire() already opened the transaction) or a plain asyncpg pool."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
            yield conn


# ── the ledger ──────────────────────────────────────────────────────────────
async def reserve(pool, items: list[tuple[str, int, Decimal, Limit | None]], *,
                  task_key: str | None = None) -> list[Reservation]:
    """Reserve every (deployment, tokens, usd, limit) in ONE transaction — all
    or nothing. Raises BudgetReached for the first model whose limited budget
    the reservation would cross (nothing is reserved then)."""
    # Same deployment twice (a fuller-text retry pair never is; a caller might) → one row update.
    merged: dict[str, list] = {}
    for dep, toks, dollars, limit in items:
        cur = merged.setdefault(dep, [0, Decimal(0), limit])
        cur[0] += int(toks)
        cur[1] += usd(dollars)
    out: list[Reservation] = []
    async with _platform_tx(pool) as conn:
        for dep in sorted(merged):                 # a fixed lock order: two pairs never deadlock
            toks, dollars, limit = merged[dep]
            await conn.execute(
                f"INSERT INTO public.ai_daily_usage (budget_day, deployment) VALUES ({BUDGET_DAY_SQL}, $1) "
                "ON CONFLICT (budget_day, deployment) DO NOTHING", dep)
            tok_cap = int(limit.value) if limit and limit.limited and limit.kind == TOKENS_PER_DAY else None
            usd_cap = limit.value if limit and limit.limited and limit.kind == USD_PER_DAY else None
            row = await conn.fetchrow(
                f"""UPDATE public.ai_daily_usage
                       SET reserved_tokens = reserved_tokens + $2::bigint,
                           reserved_usd = reserved_usd + $3::numeric, updated_at = now()
                     WHERE budget_day = {BUDGET_DAY_SQL} AND deployment = $1
                       AND ($4::bigint IS NULL OR input_tokens + output_tokens + reserved_tokens + $2::bigint
                            <= $4::bigint)
                       AND ($5::numeric IS NULL OR cost_usd + reserved_usd + $3::numeric <= $5::numeric)
                 RETURNING budget_day""",
                dep, toks, dollars, tok_cap, usd_cap)
            if row is None:
                cur = await conn.fetchrow(
                    f"SELECT budget_day, input_tokens + output_tokens + reserved_tokens AS used_tokens, "
                    f"cost_usd + reserved_usd AS used_usd FROM public.ai_daily_usage "
                    f"WHERE budget_day = {BUDGET_DAY_SQL} AND deployment = $1", dep)
                if cur is None:
                    raise LedgerWriteError(
                        f"ai_daily_usage row for {dep} is not visible after its INSERT — EITHER this connection "
                        "lacks super-admin RLS context, OR the row was removed concurrently.")
                used = (Decimal(cur["used_tokens"]) if limit.kind == TOKENS_PER_DAY
                        else Decimal(cur["used_usd"]).normalize())
                needed = Decimal(toks) if limit.kind == TOKENS_PER_DAY else dollars
                raise BudgetReached(dep, limit.kind, limit.value, used, needed, task_key, cur["budget_day"])
            out.append(Reservation(dep, row["budget_day"], toks, dollars))
    return out


async def settle(pool, res: Reservation, *, input_tokens: int, output_tokens: int, cost: Decimal,
                 calls: int = 1) -> None:
    """Replace the reservation with what was recorded. ``release`` = settle with zeros."""
    if res.done:
        return
    async with _platform_tx(pool) as conn:
        row = await conn.fetchrow(
            """UPDATE public.ai_daily_usage
                  SET reserved_tokens = GREATEST(reserved_tokens - $3::bigint, 0),
                      reserved_usd = GREATEST(reserved_usd - $4::numeric, 0),
                      input_tokens = input_tokens + $5::bigint, output_tokens = output_tokens + $6::bigint,
                      cost_usd = cost_usd + $7::numeric, calls = calls + $8::integer, updated_at = now()
                WHERE budget_day = $1::date AND deployment = $2 RETURNING 1""",
            res.budget_day, res.deployment, res.tokens, res.usd, int(input_tokens), int(output_tokens),
            usd(cost), int(calls))
    if row is None:
        # Zero rows does not say which: never name one cause (CLAUDE.md).
        raise LedgerWriteError(
            f"settling {res.deployment} for {res.budget_day} matched zero rows — EITHER the connection lacks "
            "super-admin RLS context (the UPDATE policy silently matches nothing), OR the row was deleted.")
    res.done = True


async def release(pool, res: Reservation) -> None:
    await settle(pool, res, input_tokens=0, output_tokens=0, cost=Decimal(0), calls=0)


async def usage_today(conn, deployments=None) -> dict[str, dict]:
    """{deployment: today's shared usage} — the caller's connection must carry
    super-admin context (the picker's request does)."""
    rows = await conn.fetch(
        f"SELECT * FROM public.ai_daily_usage WHERE budget_day = {BUDGET_DAY_SQL} "
        "AND ($1::text[] IS NULL OR deployment = ANY($1::text[]))",
        list(deployments) if deployments is not None else None)
    return {r["deployment"]: usage_out(r) for r in rows}


def usage_out(r) -> dict:
    return {"budget_day": r["budget_day"].isoformat(), "input_tokens": int(r["input_tokens"]),
            "output_tokens": int(r["output_tokens"]),
            "total_tokens": int(r["input_tokens"]) + int(r["output_tokens"]),
            "cost_usd": str(Decimal(r["cost_usd"]).quantize(Decimal("0.000001"))),
            "reserved_tokens": int(r["reserved_tokens"]),
            "reserved_usd": str(Decimal(r["reserved_usd"]).quantize(Decimal("0.000001"))),
            "calls": int(r["calls"])}


# ── the scope a job's calls run under ───────────────────────────────────────
@dataclass
class BudgetScope:
    pool: object
    task_key: str
    limits: dict[str, Limit]
    catalog: dict = field(default_factory=dict)   # {deployment: proxy.Deployment}, manual prices applied
    ensemble_config_id: str | None = None
    usage: dict[str, dict] = field(default_factory=dict)
    _prepaid: dict[str, list[Reservation]] = field(default_factory=dict)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    def limit(self, deployment: str) -> Limit | None:
        return self.limits.get(deployment)

    def check_enforceable(self) -> None:
        """A dollar budget needs a price; refuse before any call otherwise."""
        from services.note_extraction.spend import has_price

        bad = [d for d, lim in self.limits.items()
               if lim.limited and lim.kind == USD_PER_DAY and not has_price(self.catalog.get(d))]
        if bad:
            raise BudgetConfigError(
                "a dollar budget cannot be enforced for a model with no price on the proxy and no manual price "
                "on its platform_model_catalog entry: " + ", ".join(sorted(bad)))

    def estimate(self, deployment: str, prompt_chars: int, max_tokens: int) -> tuple[int, Decimal]:
        from services.note_extraction.spend import estimate_call_cost

        return (estimate_tokens(prompt_chars, max_tokens),
                usd(estimate_call_cost(self.catalog.get(deployment), prompt_chars, max_tokens)))

    async def reserve_items(self, items: list[tuple[str, int, int]]) -> list[Reservation]:
        """items: (deployment, prompt_chars, max_tokens)."""
        planned = []
        for dep, chars, mt in items:
            t, d = self.estimate(dep, chars, mt)
            planned.append((dep, t, d, self.limit(dep)))
        return await reserve(self.pool, planned, task_key=self.task_key)

    @asynccontextmanager
    async def pair(self, items: list[tuple[str, int, int]]):
        """Reserve every model of an ensemble read at once (all or nothing),
        then let each call consume its own reservation."""
        made = await self.reserve_items(items)
        async with self._lock:
            for r in made:
                self._prepaid.setdefault(r.deployment, []).append(r)
        try:
            yield made
        finally:
            async with self._lock:
                left = [r for r in made if not r.done]
                for r in left:
                    q = self._prepaid.get(r.deployment) or []
                    if r in q:
                        q.remove(r)
            for r in left:
                await release(self.pool, r)

    async def _take_prepaid(self, deployment: str) -> Reservation | None:
        async with self._lock:
            q = self._prepaid.get(deployment) or []
            while q:
                r = q.pop(0)
                if not r.done:
                    return r
        return None

    async def before_call(self, body: dict) -> Reservation:
        """Called by proxy.chat before the request. May set body['max_tokens']."""
        dep = body.get("model")
        lim = self.limit(dep)
        mt = body_max_tokens(body)
        if mt is None:
            mt = default_max_tokens()
            if lim is not None and lim.limited:
                body["max_tokens"] = mt        # a real output ceiling under a limited budget
        pre = await self._take_prepaid(dep)
        if pre is not None:
            return pre
        (r,) = await self.reserve_items([(dep, prompt_chars_of(body.get("messages")), mt)])
        return r

    async def after_call(self, res: Reservation, resp) -> None:
        """Replace the reservation with the provider-reported usage."""
        from services.note_extraction import proxy
        from services.note_extraction.spend import recorded_cost

        tin, tout, cached = proxy.response_usage(getattr(resp, "body", None))
        ok = getattr(resp, "status", 0) == 200 and getattr(resp, "body", None) is not None
        if tin is None or tout is None:
            if ok:      # answered but reported no usage: keep the reservation as the recorded usage
                tin, tout, cost = res.tokens, 0, res.usd
            else:       # refused / unreachable: nothing was billed
                tin, tout, cost = 0, 0, Decimal(0)
        else:
            c = recorded_cost(self.catalog.get(res.deployment), proxy.header_cost(resp), tin, tout, cached)
            cost = usd(c) if c is not None else Decimal(0)
        await settle(self.pool, res, input_tokens=int(tin), output_tokens=int(tout), cost=cost)
        async with self._lock:
            u = self.usage.setdefault(res.deployment, {"input_tokens": 0, "output_tokens": 0,
                                                       "cost_usd": Decimal(0), "calls": 0})
            u["input_tokens"] += int(tin)
            u["output_tokens"] += int(tout)
            u["cost_usd"] += cost
            u["calls"] += 1

    async def abandon(self, res: Reservation) -> None:
        await release(self.pool, res)

    def usage_json(self) -> dict:
        return {d: {**u, "cost_usd": str(u["cost_usd"])} for d, u in self.usage.items()}


_CURRENT: contextvars.ContextVar[BudgetScope | None] = contextvars.ContextVar("ai_budget_scope", default=None)


def current() -> BudgetScope | None:
    return _CURRENT.get()


@asynccontextmanager
async def activate(scope: BudgetScope):
    token = _CURRENT.set(scope)
    try:
        yield scope
    finally:
        _CURRENT.reset(token)


@asynccontextmanager
async def pair(items: list[tuple[str, int, int]]):
    """Module-level pair: a no-op outside a budget scope."""
    scope = current()
    if scope is None:
        yield []
        return
    async with scope.pair(items) as made:
        yield made


async def priced_catalog(conn, catalog: dict | None) -> dict:
    from services.note_extraction.spend import apply_manual_prices, load_manual_prices

    return apply_manual_prices(catalog or {}, await load_manual_prices(conn))


@asynccontextmanager
async def job_scope(task_key: str, ensemble: dict | None, *, catalog: dict | None = None, pool=None,
                    catalog_loader=None):
    """The scope a job's calls run under. Reuses the active scope when it is
    already this task's (the daily run opens it first to collect usage);
    otherwise builds one from the ensemble row's budgets. The catalog is
    loaded only when the caller did not pass one and a dollar budget needs a
    price; a dollar budget on an unpriced model raises BudgetConfigError."""
    cur = current()
    if cur is not None and cur.task_key == task_key:
        yield cur
        return
    if pool is None:
        from services.database import get_pool

        pool = await get_pool()
    limits = limits_from_config(ensemble)
    if catalog is None and any(lim.limited and lim.kind == USD_PER_DAY for lim in limits.values()):
        from services.note_extraction import proxy

        try:
            catalog = await asyncio.to_thread(catalog_loader or proxy.deployment_catalog)
        except Exception as exc:  # noqa: BLE001
            raise BudgetConfigError(f"the proxy's price list is unreadable ({type(exc).__name__}) — a dollar "
                                    "budget cannot be enforced without it") from exc
    async with pool.acquire() as conn:
        priced = await priced_catalog(conn, catalog)
    scope = BudgetScope(pool=pool, task_key=task_key, limits=limits, catalog=priced,
                        ensemble_config_id=str(ensemble["id"]) if ensemble and ensemble.get("id") else None)
    scope.check_enforceable()
    async with activate(scope):
        yield scope
