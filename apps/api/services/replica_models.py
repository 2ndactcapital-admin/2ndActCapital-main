"""Replication Studio (rep4) — build a model from a fund.

A registry entry (rep1) with an APPROVED terms version and a LIVE holdings
snapshot becomes a "Holdings of <TICKER>" model in rep3's one model registry
(source 'fund_replica'). Nothing here trades: the build is a DRAFT version that
rep3's maker-checker human task approves (approve_models), and approval emits
'model.version_approved' for rep5 to turn into proposals (the custody cliff).

FIND OR BUILD — the registry key
──────────────────────────────────────────────────────────────────────────────
One live model per (org, entry, custodian, method, top N)
(``portfolio.replica_registry_keys``, partial unique). A build whose key
already has a model returns THAT model ("run in the same fund") and logs
``fund_build_joined`` — personal settings live on the account (rep5), so a new
model is built only when the construction key differs. The name is rep1's
display name; a top-N variant is suffixed (``Holdings of QQQ — Top 50``); a
second custodian with the same method gets the custodian's label appended,
because model names are unique per org.

CONSTRUCTION (:func:`construct`, pure, exact Decimal)
──────────────────────────────────────────────────────────────────────────────
From the snapshot's rows, in this order:
  1. only EQUITY rows are positions — the fund's own cash rows are replaced by
     the model's cash target; futures / other are dropped (a departure when
     they carry weight: the model holds stocks only);
  2. unresolved equity rows: ``block`` keeps them as unresolved positions (rep3
     refuses to approve a version with unresolved rows until they are
     resolved); ``exclude_and_reweight`` drops them (a recorded departure);
  3. ``top_n``: the N largest by fund weight (ties by row number);
  4. ``min_weight``: positions whose FUND weight is under it are dropped and
     their weight is spread pro rata over the rest (a recorded departure);
  5. the remaining fund weights are normalised to 1 − cash_target, quantised
     to numeric(14,10) by largest remainder (ties by row number) so the
     positions sum to exactly 1 − cash_target; a cash position holds the rest.
Every dropped row is listed with its reason; every deliberate difference from
the fund is a departure in plain words, stored on the spec.

MONITORING
──────────────────────────────────────────────────────────────────────────────
* A new LIVE snapshot of fund F (rep1's weekday refresh and the manual
  refresh both call :func:`monitor_snapshot`): for every fund_replica model of
  F in every org, the would-be version is built with the live version's spec;
  turnover against the live version ≥ the spec's turnover_review_threshold, or
  any constituent added / removed → a system-opened DRAFT (origin
  'fund_monitor') + rep3's review task (``model_version_review``) carrying the
  diff; otherwise "within tolerance", no draft. Idempotent per (model,
  snapshot) IN THE DATABASE (``model_events_holdings_monitor_uq``).
* A new APPROVED terms version whose critical fields changed (expense ratio,
  weighting method, rebalance frequency, index name): one review task
  (``fund_model_terms_review``, outcome 'done', manage_models) per model of the
  fund naming the changed fields — no automatic version. Idempotent per (model,
  terms version) (``model_events_terms_monitor_uq``).
Every result is a model event (+ audit row) and a domain event.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from decimal import ROUND_FLOOR, Decimal, localcontext

import asyncpg

from services import custodian_capabilities as CC
from services import human_tasks as HT
from services import model_upload as U
from services import models as M
from services import replica_holdings as H
from services import replica_terms as T

log = logging.getLogger(__name__)

T_SPEC = "portfolio.replica_model_specs"
T_KEY = "portfolio.replica_registry_keys"

Q = U.Q                                    # numeric(14, 10)
ZERO = Decimal(0)
ONE = Decimal(1)

METHOD_LABELS = {"full": "Every holding", "top_n": "Largest holdings only (top N)"}
UNRESOLVED_POLICY_LABELS = {
    "block": "Keep them — the draft cannot be approved until they are resolved",
    "exclude_and_reweight": "Leave them out and reweight the rest (a departure)",
}
DROP_REASON_LABELS = {
    "cash": "The fund's own cash — the model holds its cash target instead",
    "not_equity": "Not a stock (futures / other) — the model holds stocks only",
    "unresolved_excluded": "Not matched to a security — left out (unresolved policy)",
    "below_top_n": "Outside the largest N holdings",
    "below_min_weight": "Under the minimum weight — its weight is spread over the rest",
    "no_weight": "The holdings file gives it no weight",
}
DEPARTURE_LABELS = {
    "non_equity_excluded": "Stocks only", "exclude_unresolved": "Unmatched holdings left out",
    "top_n": "Largest holdings only", "min_weight": "Minimum weight", "cash_target": "Cash target",
    "rebalance_frequency": "Rebalance frequency",
}
MONITOR_OUTCOME_LABELS = {
    "drafted": "New version drafted for review", "within_tolerance": "Within tolerance — no draft",
    "skipped_open_draft": "Not drafted — the model already has an open draft",
    "no_live_version": "Not drafted — the model has no approved version yet",
    "not_buildable": "Not drafted — nothing could be built from the snapshot",
}
REBALANCE_ORIGIN_LABELS = {"terms": "From the fund's terms", "deliberate": "A deliberate change",
                           "not_stated": "The fund's terms do not state it"}

#: Approved-terms fields whose change raises a review task on every model of the fund.
CRITICAL_TERMS = ("expense_ratio_net_pct", "expense_ratio_gross_pct", "weighting_method", "rebalance_frequency",
                  "index_name")
TERMS_REVIEW_KIND = "fund_model_terms_review"
TERMS_REVIEW_SUBJECT = "model"

#: rep5: the custodian suffix a variant's NAME carries ("Holdings of QQQ —
#: Manual"). The capability matrix keeps its own longer label
#: (custodian_capabilities.CUSTODIAN_LABELS) — a model name is not a route.
VARIANT_NAME_LABELS = {"manual": "Manual"}

DRIFT_SETTING = "replication.models.drift_threshold"
TURNOVER_SETTING = "replication.models.turnover_review_threshold"
DEFAULT_DRIFT = Decimal("0.01")          # absolute weight drift per position before a rebalance (rep5 reads it)
DEFAULT_TURNOVER = Decimal("0.02")       # the same 2% rep1 uses for its holdings review


class BuildPrecondition(M.ModelConflict):
    """409: the entry lacks an approved terms version and/or a live snapshot."""

    def __init__(self, missing: list[str]):
        self.missing = missing
        super().__init__("cannot build a model yet — " + " and ".join(missing))


# ── parameters ──────────────────────────────────────────────────────────────
@dataclass
class BuildParams:
    custodian_code: str
    method: str = "full"
    top_n: int | None = None
    min_weight: Decimal = ZERO
    cash_target: Decimal = ZERO
    unresolved_policy: str = "block"
    rebalance_frequency: str | None = None      # None → the fund's (terms)
    rebalance_reason: str | None = None
    drift_threshold: Decimal | None = None      # None → org setting
    turnover_review_threshold: Decimal | None = None

    def key(self) -> tuple:
        return (self.custodian_code, self.method, self.top_n)


def _pct(v, what: str, *, lo: Decimal = ZERO, hi: Decimal = Decimal(100), lo_open: bool = False) -> Decimal | None:
    """A percent string → an exact fraction, refused beyond numeric(14,10)."""
    try:
        d = U.to_decimal(v)
    except ValueError as exc:
        raise M.ModelError(f"{what}: {exc}") from None
    if d is None:
        return None
    if d < lo or d >= hi or (lo_open and d == lo):
        raise M.ModelError(f"{what} must be {'above' if lo_open else 'at least'} {lo}% and under {hi}%")
    f = d / 100
    if f != f.quantize(Q):
        raise M.ModelError(f"{what}: at most 8 decimal places of a percent")
    return f


def params_from_input(*, custodian_code: str, method: str = "full", top_n=None, min_weight_pct=None,
                      cash_target_pct=None, unresolved_policy: str = "block", rebalance_frequency: str | None = None,
                      rebalance_reason: str | None = None, drift_threshold_pct=None,
                      turnover_review_threshold_pct=None) -> BuildParams:
    code = (custodian_code or "").strip().lower()
    if code not in CC.CUSTODIAN_LABELS:
        raise M.ModelError(f"custodian is one of {list(CC.CUSTODIAN_LABELS)}")
    if method not in METHOD_LABELS:
        raise M.ModelError(f"method is one of {list(METHOD_LABELS)}")
    if method == "top_n":
        if top_n is None or isinstance(top_n, bool) or int(top_n) < 1:
            raise M.ModelError("top N needs a number of holdings, 1 or more")
        top_n = int(top_n)
    elif top_n is not None:
        raise M.ModelError("top N applies only to the top_n method")
    if unresolved_policy not in UNRESOLVED_POLICY_LABELS:
        raise M.ModelError(f"unresolved policy is one of {list(UNRESOLVED_POLICY_LABELS)}")
    freq = (rebalance_frequency or "").strip() or None
    if freq and not (rebalance_reason or "").strip():
        raise M.ModelError("rebalancing on a different schedule from the fund is a departure — give a reason")
    return BuildParams(
        custodian_code=code, method=method, top_n=top_n,
        min_weight=_pct(min_weight_pct, "minimum weight") or ZERO,
        cash_target=_pct(cash_target_pct, "cash target") or ZERO,
        unresolved_policy=unresolved_policy, rebalance_frequency=freq,
        rebalance_reason=(rebalance_reason or "").strip() or None,
        drift_threshold=_pct(drift_threshold_pct, "drift threshold", lo_open=True, hi=Decimal("100.0000001")),
        turnover_review_threshold=_pct(turnover_review_threshold_pct, "turnover review threshold", lo_open=True,
                                       hi=Decimal("100.0000001")))


def params_from_spec(spec: dict) -> BuildParams:
    return BuildParams(
        custodian_code=spec["custodian_code"], method=spec["method"], top_n=spec["top_n"],
        min_weight=Decimal(spec["min_weight"]), cash_target=Decimal(spec["cash_target"]),
        unresolved_policy=spec["unresolved_policy"],
        rebalance_frequency=spec["rebalance_frequency"] if spec["rebalance_origin"] == "deliberate" else None,
        rebalance_reason=next((d.get("detail", {}).get("reason") for d in (M._loads(spec["departures"]) or [])
                               if d.get("kind") == "rebalance_frequency"), None),
        drift_threshold=Decimal(spec["drift_threshold"]),
        turnover_review_threshold=Decimal(spec["turnover_review_threshold"]))


async def _setting(conn, org_id, key: str, default: Decimal) -> tuple[Decimal, str]:
    from services.org_settings import get_setting_with_origin

    try:
        v, is_default = await get_setting_with_origin(conn, uuid.UUID(str(org_id)), key)
    except Exception:  # noqa: BLE001 — an unreadable setting falls back to the stated default
        return default, "default"
    if v is None or is_default:
        return default, "default"
    try:
        d = Decimal(str(v))
    except Exception:  # noqa: BLE001
        return default, "default"
    return (d, "org_setting") if ZERO < d <= ONE else (default, "default")


async def resolve_thresholds(conn, org_id, p: BuildParams) -> BuildParams:
    if p.drift_threshold is None:
        p.drift_threshold, _ = await _setting(conn, org_id, DRIFT_SETTING, DEFAULT_DRIFT)
    if p.turnover_review_threshold is None:
        p.turnover_review_threshold, _ = await _setting(conn, org_id, TURNOVER_SETTING, DEFAULT_TURNOVER)
    return p


# ── construction (pure) ─────────────────────────────────────────────────────
def _w(v) -> Decimal | None:
    return None if v is None else Decimal(str(v))


def _row_key(r: dict) -> str:
    if r.get("constituent_global_security_id"):
        return str(r["constituent_global_security_id"])
    for k in ("raw_cusip", "raw_isin", "raw_sedol", "raw_ticker", "raw_name"):
        if r.get(k):
            return f"unresolved:{str(r[k]).upper()}"
    return f"unresolved:row{r['row_num']}"


def _label(r: dict) -> str:
    return r.get("raw_ticker") or r.get("raw_name") or r.get("raw_cusip") or f"row {r['row_num']}"


def allocate(weights: list[tuple[str, Decimal, int]], total: Decimal) -> dict[str, Decimal]:
    """Normalise ``weights`` [(key, weight, tiebreak)] to sum to exactly ``total``
    at numeric(14,10): each share floored to 1e-10, the left-over units to the
    largest remainders (ties by ``tiebreak``, ascending). Exact Decimal."""
    if total != total.quantize(Q):
        raise M.ModelError("the invested total must have at most 10 decimal places")
    s = sum((w for _, w, _ in weights), ZERO)
    if s <= 0:
        raise M.ModelError("the holdings carry no weight to build from")
    with localcontext() as ctx:
        ctx.prec = 60
        units = total / Q
        raw = [(k, w * units / s, t) for k, w, t in weights]
        floors = {k: u.to_integral_value(rounding=ROUND_FLOOR) for k, u, _ in raw}
        left = units - sum(floors.values(), ZERO)
        order = sorted(raw, key=lambda x: (-(x[1] - floors[x[0]]), x[2]))
        for i in range(int(left)):
            floors[order[i][0]] += 1
        return {k: (floors[k] * Q).quantize(Q) for k, _, _ in raw}


@dataclass
class Construction:
    positions: list[dict] = field(default_factory=list)
    dropped: list[dict] = field(default_factory=list)
    departures: list[dict] = field(default_factory=list)
    counts: dict = field(default_factory=dict)
    fund_rows: list[dict] = field(default_factory=list)


def _pct_text(d: Decimal) -> str:
    return U.pct_text(d)


def construct(rows: list[dict], p: BuildParams) -> Construction:
    """The model's positions from a snapshot's rows (module docstring, steps 1–5)."""
    c = Construction()
    if p.method == "top_n" and (p.top_n is None or p.top_n < 1):
        raise M.ModelError("top N needs a number of holdings, 1 or more")
    if not (ZERO <= p.cash_target < ONE) or not (ZERO <= p.min_weight < ONE):
        raise M.ModelError("cash target and minimum weight are fractions in [0, 1)")
    fund_total = sum((_w(r["weight"]) or ZERO for r in rows if r["asset_type"] != "cash"), ZERO)

    def drop(r, reason):
        c.dropped.append({"row_num": r["row_num"], "label": _label(r), "name": r.get("raw_name"),
                          "ticker": r.get("raw_ticker"), "weight": str(_w(r["weight"])) if r["weight"] is not None
                          else None, "reason": reason})

    candidates: dict[str, dict] = {}
    non_equity = ZERO
    unresolved_out: list[dict] = []
    for r in sorted(rows, key=lambda x: x["row_num"]):
        w = _w(r["weight"])
        if r["asset_type"] == "cash":
            drop(r, "cash")
            continue
        if r["asset_type"] != "equity":
            non_equity += w or ZERO
            drop(r, "not_equity")
            continue
        if w is None or w <= 0:
            drop(r, "no_weight")
            continue
        state = "resolved" if r["resolution_state"] == "resolved" else "unresolved"
        if state == "unresolved" and p.unresolved_policy == "exclude_and_reweight":
            unresolved_out.append(r)
            drop(r, "unresolved_excluded")
            continue
        k = _row_key(r)
        if k in candidates:               # two rows of the same security: one position, weights summed
            candidates[k]["weight"] += w
            continue
        candidates[k] = {"key": k, "row": r, "weight": w, "state": state}
    equity_total = sum((x["weight"] for x in candidates.values()), ZERO) + sum(
        (_w(r["weight"]) for r in unresolved_out), ZERO)
    if non_equity > 0:
        c.departures.append({"kind": "non_equity_excluded",
                             "text": f"The fund holds {_pct_text(non_equity)} in futures and other non-stock "
                                     "instruments; the model holds stocks only.",
                             "detail": {"weight": str(non_equity)}})
    if unresolved_out:
        uw = sum((_w(r["weight"]) for r in unresolved_out), ZERO)
        c.departures.append({"kind": "exclude_unresolved",
                             "text": f"{len(unresolved_out)} holding(s) ({_pct_text(uw)} of the fund) could not be "
                                     "matched to a security and are left out; the rest are reweighted.",
                             "detail": {"rows": [r["row_num"] for r in unresolved_out], "weight": str(uw)}})
    ranked = sorted(candidates.values(), key=lambda x: (-x["weight"], x["row"]["row_num"]))
    if p.method == "top_n" and len(ranked) > p.top_n:
        kept, cut = ranked[:p.top_n], ranked[p.top_n:]
        for x in cut:
            drop(x["row"], "below_top_n")
        kw = sum((x["weight"] for x in kept), ZERO)
        c.departures.append({"kind": "top_n",
                             "text": f"Holds the largest {p.top_n} of {len(ranked)} stocks ({_pct_text(kw)} of the "
                                     "fund), reweighted to the invested total.",
                             "detail": {"top_n": p.top_n, "of": len(ranked), "kept_weight": str(kw)}})
        ranked = kept
    if p.min_weight > 0:
        under = [x for x in ranked if x["weight"] < p.min_weight]
        if under:
            for x in under:
                drop(x["row"], "below_min_weight")
            uw = sum((x["weight"] for x in under), ZERO)
            c.departures.append({"kind": "min_weight",
                                 "text": f"Holdings under {_pct_text(p.min_weight)} are dropped ({len(under)} "
                                         f"holding(s), {_pct_text(uw)} of the fund) and their weight is spread pro "
                                         "rata over the rest.",
                                 "detail": {"min_weight": str(p.min_weight), "rows": [x["row"]["row_num"] for x in
                                                                                      under], "weight": str(uw)}})
            ranked = [x for x in ranked if x["weight"] >= p.min_weight]
    if not ranked:
        raise M.ModelError("nothing is left to hold after these construction choices")
    invested = ONE - p.cash_target
    alloc = allocate([(x["key"], x["weight"], x["row"]["row_num"]) for x in ranked], invested)
    for i, x in enumerate(ranked, start=1):
        r = x["row"]
        c.positions.append({c_: None for c_ in M.POS_COLS} | {
            "row_num": i,
            "global_security_id": str(r["constituent_global_security_id"]) if x["state"] == "resolved" else None,
            "raw_ticker": r.get("raw_ticker"), "raw_cusip": r.get("raw_cusip"), "raw_isin": r.get("raw_isin"),
            "raw_sedol": r.get("raw_sedol"), "raw_name": r.get("raw_name"), "target_weight": alloc[x["key"]],
            "asset_class": "Equity", "alternates": [], "resolution_state": x["state"],
            "resolution_note": None if x["state"] == "resolved" else
            "not matched to a security in the fund's holdings file — resolve it before approval"})
    if p.cash_target > 0:
        c.positions.append({c_: None for c_ in M.POS_COLS} | {
            "row_num": len(c.positions) + 1, "raw_name": "Cash", "target_weight": p.cash_target, "alternates": [],
            "resolution_state": "cash", "asset_class": "Cash"})
        c.departures.append({"kind": "cash_target", "text": f"Holds {_pct_text(p.cash_target)} in cash.",
                             "detail": {"cash_target": str(p.cash_target)}})
    model_w = {x["row"]["row_num"]: alloc[x["key"]] for x in ranked}
    c.fund_rows = [{"row_num": r["row_num"], "label": _label(r), "name": r.get("raw_name"),
                    "ticker": r.get("raw_ticker"), "asset_type": r["asset_type"],
                    "resolution_state": r["resolution_state"],
                    "fund_weight": str(_w(r["weight"])) if r["weight"] is not None else None,
                    "model_weight": str(model_w[r["row_num"]]) if r["row_num"] in model_w else None,
                    "dropped_reason": next((d["reason"] for d in c.dropped if d["row_num"] == r["row_num"]), None)}
                   for r in sorted(rows, key=lambda x: (-(_w(x["weight"]) or ZERO), x["row_num"]))]
    c.counts = {"fund_rows": len(rows), "positions": len(c.positions),
                "resolved": sum(1 for x in c.positions if x["resolution_state"] == "resolved"),
                "unresolved": sum(1 for x in c.positions if x["resolution_state"] == "unresolved"),
                "dropped": len(c.dropped), "fund_weight_total_ex_cash": str(fund_total),
                "fund_equity_weight": str(equity_total),
                "invested_total": str(invested),
                "weight_total": str(sum((Decimal(str(x["target_weight"])) for x in c.positions), ZERO))}
    return c


def construction_out(c: Construction) -> dict:
    return {"positions": [{"row_num": x["row_num"], "global_security_id": x["global_security_id"],
                           "ticker": x["raw_ticker"], "name": x["raw_name"], "cusip": x["raw_cusip"],
                           "target_weight": str(x["target_weight"]), "resolution_state": x["resolution_state"]}
                          for x in c.positions],
            "dropped": c.dropped, "departures": c.departures, "counts": c.counts, "fund_rows": c.fund_rows}


# ── sources ─────────────────────────────────────────────────────────────────
@dataclass
class Sources:
    entry: dict
    terms: dict | None
    snapshot: dict | None
    rows: list[dict]

    def missing(self) -> list[str]:
        out = []
        if self.terms is None:
            out.append("no approved terms version")
        if self.snapshot is None:
            out.append("no live holdings snapshot")
        return out


async def load_sources(conn, org_id, entry_id) -> Sources:
    try:
        entry = await T.load_entry(conn, org_id, entry_id)
    except T.ReplicaNotFound as exc:
        raise M.ModelNotFound(str(exc)) from None
    terms = await T.live_approved(conn, entry["id"])
    snap = await H.latest_snapshot(conn, str(entry["global_security_id"]), entry["holdings_source_key"],
                                   live_only=True)
    rows = await H.snapshot_rows(conn, snap["id"]) if snap else []
    return Sources(entry, terms, snap, rows)


def term_value(terms: dict | None, key: str):
    if not terms:
        return None
    v = (M._loads(terms["term_values"]) or {}).get(key) or {}
    return v.get("value")


def _rebalance(terms: dict | None, p: BuildParams) -> tuple[str | None, str, dict | None]:
    fund = term_value(terms, "rebalance_frequency")
    if p.rebalance_frequency and p.rebalance_frequency != fund:
        return p.rebalance_frequency, "deliberate", {
            "kind": "rebalance_frequency",
            "text": f"Rebalances {p.rebalance_frequency} instead of the fund's {fund or 'unstated schedule'}"
                    + (f" — {p.rebalance_reason}" if p.rebalance_reason else "") + ".",
            "detail": {"fund_value": fund, "our_value": p.rebalance_frequency, "reason": p.rebalance_reason}}
    if fund:
        return str(fund), "terms", None
    return None, "not_stated", None


# ── the registry ────────────────────────────────────────────────────────────
def _key_args(p: BuildParams) -> tuple:
    return (p.custodian_code, p.method, p.top_n)


async def _versions_brief(conn, model_id) -> tuple[dict | None, dict | None]:
    return await M.live_approved(conn, model_id), await M.open_version(conn, model_id)


async def spec_for_version(conn, version_id) -> dict | None:
    row = await conn.fetchrow(f"SELECT * FROM {T_SPEC} WHERE version_id = $1::uuid AND valid_to IS NULL "
                              "AND system_to IS NULL", str(version_id))
    return dict(row) if row else None


async def latest_spec(conn, model_id) -> dict | None:
    """The live approved version's spec, else the newest spec of the model."""
    live = await M.live_approved(conn, model_id)
    if live is not None:
        s = await spec_for_version(conn, live["id"])
        if s is not None:
            return s
    row = await conn.fetchrow(f"SELECT * FROM {T_SPEC} WHERE model_id = $1::uuid AND valid_to IS NULL AND system_to "
                              "IS NULL ORDER BY created_at DESC, id LIMIT 1", str(model_id))
    return dict(row) if row else None


def spec_out(s: dict | None) -> dict | None:
    if not s:
        return None
    return {"id": str(s["id"]), "model_id": str(s["model_id"]), "version_id": str(s["version_id"]),
            "replica_security_id": str(s["replica_security_id"]), "terms_version_id": str(s["terms_version_id"]),
            "snapshot_id": str(s["snapshot_id"]), "custodian_code": s["custodian_code"], "method": s["method"],
            "top_n": s["top_n"], "min_weight": str(s["min_weight"]), "cash_target": str(s["cash_target"]),
            "unresolved_policy": s["unresolved_policy"], "rebalance_frequency": s["rebalance_frequency"],
            "rebalance_origin": s["rebalance_origin"], "drift_threshold": str(s["drift_threshold"]),
            "turnover_review_threshold": str(s["turnover_review_threshold"]),
            "departures": M._loads(s["departures"]) or [], "dropped": M._loads(s["dropped"]) or [],
            "origin": s["origin"], "created_by": str(s["created_by"]) if s["created_by"] else None,
            "created_at": M._iso(s["created_at"])}


async def _model_summary(conn, key_row: dict) -> dict:
    model = await M.load_model(conn, key_row["org_id"], key_row["model_id"])
    live, opn = await _versions_brief(conn, model["id"])
    totals = M._loads(live["totals"]) if live else None
    return {
        "model": M.model_out(model),
        "key": {"custodian_code": key_row["custodian_code"], "method": key_row["method"], "top_n": key_row["top_n"]},
        "live_version": {"id": str(live["id"]), "version_no": live["version_no"],
                         "approved_at": M._iso(live["approved_at"])} if live else None,
        "open_version": {"id": str(opn["id"]), "version_no": opn["version_no"], "status": opn["status"],
                         "origin": opn["origin"]} if opn else None,
        "positions": (totals or {}).get("positions") if live else None,
        # accounts are linked to models in rep5; until then no account uses any model
        "accounts_using": 0,
        "spec": spec_out(await latest_spec(conn, model["id"])),
    }


async def find_model(conn, org_id, entry_id, p: BuildParams) -> dict | None:
    row = await conn.fetchrow(
        f"SELECT * FROM {T_KEY} WHERE org_id = $1::uuid AND replica_security_id = $2::uuid AND custodian_code = $3 "
        "AND method = $4 AND COALESCE(top_n, 0) = COALESCE($5::int, 0) AND valid_to IS NULL AND system_to IS NULL",
        str(org_id), str(entry_id), *_key_args(p))
    return await _model_summary(conn, dict(row)) if row else None


async def models_of_entry(conn, org_id, entry_id) -> list[dict]:
    rows = await conn.fetch(f"SELECT * FROM {T_KEY} WHERE org_id = $1::uuid AND replica_security_id = $2::uuid "
                            "AND valid_to IS NULL AND system_to IS NULL ORDER BY custodian_code, method, top_n",
                            str(org_id), str(entry_id))
    return [await _model_summary(conn, dict(r)) for r in rows]


async def _model_name(conn, org_id, entry: dict, p: BuildParams) -> str:
    base = T.display_name_for(entry["ticker"])
    if p.method == "top_n":
        base = f"{base} — Top {p.top_n}"
    suffix = VARIANT_NAME_LABELS.get(p.custodian_code) or CC.CUSTODIAN_LABELS.get(p.custodian_code, p.custodian_code)
    for name in (base, f"{base} — {suffix}"):
        taken = await conn.fetchval(f"SELECT 1 FROM {M.T_MODEL} WHERE org_id = $1::uuid AND lower(btrim(name)) = "
                                    "lower($2) AND valid_to IS NULL AND system_to IS NULL", str(org_id), name)
        if not taken:
            return name
    raise M.ModelConflict(f"the names {base!r} and its custodian variant are both taken in this organization")


def _differences(spec: dict | None, p: BuildParams) -> list[str]:
    if not spec:
        return []
    out = []
    for k in ("min_weight", "cash_target"):
        if Decimal(spec[k]) != getattr(p, k):
            out.append(k)
    if spec["unresolved_policy"] != p.unresolved_policy:
        out.append("unresolved_policy")
    return out


async def _insert_spec(conn, *, org_id, model_id, version_id, entry: dict, terms_version_id, snapshot_id,
                       p: BuildParams, rebalance: tuple, c: Construction, origin: str, created_by) -> dict:
    freq, rorigin, rdep = rebalance
    departures = list(c.departures) + ([rdep] if rdep else [])
    row = await conn.fetchrow(
        f"""INSERT INTO {T_SPEC} (org_id, model_id, version_id, replica_security_id, terms_version_id, snapshot_id,
                custodian_code, method, top_n, min_weight, cash_target, unresolved_policy, rebalance_frequency,
                rebalance_origin, drift_threshold, turnover_review_threshold, departures, dropped, origin, created_by)
            VALUES ($1::uuid, $2::uuid, $3::uuid, $4::uuid, $5::uuid, $6::uuid, $7, $8, $9, $10, $11, $12, $13, $14,
                    $15, $16, $17::jsonb, $18::jsonb, $19, $20::uuid) RETURNING *""",
        str(org_id), str(model_id), str(version_id), str(entry["id"]), str(terms_version_id), str(snapshot_id),
        p.custodian_code, p.method, p.top_n, p.min_weight, p.cash_target, p.unresolved_policy, freq, rorigin,
        p.drift_threshold, p.turnover_review_threshold, M._dumps(departures), M._dumps(c.dropped), origin,
        str(created_by) if created_by else None)
    return dict(row)


# ── preview / build ─────────────────────────────────────────────────────────
async def preview(pool, *, org_id, entry_id, p: BuildParams) -> dict:
    """What a build would create — positions vs the fund's holdings, dropped
    rows, departures, the name, and the existing model for this key. Writes
    nothing."""
    async with pool.acquire() as conn:
        src = await load_sources(conn, org_id, entry_id)
        if src.missing():
            raise BuildPrecondition(src.missing())
        await resolve_thresholds(conn, org_id, p)
        existing = await find_model(conn, org_id, src.entry["id"], p)
        c = construct(src.rows, p)
        rebalance = _rebalance(src.terms, p)
        name = None if existing else await _model_name(conn, org_id, src.entry, p)
    return {"existing": existing, "name": name, "construction": construction_out(c),
            "departures": c.departures + ([rebalance[2]] if rebalance[2] else []),
            "rebalance": {"frequency": rebalance[0], "origin": rebalance[1]},
            "snapshot": {"id": str(src.snapshot["id"]), "as_of": src.snapshot["as_of"].isoformat(),
                         "source_key": src.snapshot["source_key"]},
            "terms_version": {"id": str(src.terms["id"]), "version_no": src.terms["version_no"]},
            "differences_from_existing": _differences(existing["spec"], p) if existing else []}


async def build_model(pool, *, org_id, actor_id, entry_id, p: BuildParams, description: str | None = None) -> dict:
    """Find or build. An existing model for the key is returned (joined=True);
    otherwise the fund_replica model, its registry key, a DRAFT version (origin
    'fund_build', maker = the builder) and its spec — one transaction."""
    refs: list[M.EventRef] = []
    try:
        async with pool.acquire() as conn:
            actor = await HT.assert_human(conn, actor_id, org_id)
            src = await load_sources(conn, org_id, entry_id)
            if src.missing():
                raise BuildPrecondition(src.missing())
            await resolve_thresholds(conn, org_id, p)
            existing = await find_model(conn, org_id, src.entry["id"], p)
            if existing is not None:
                diffs = _differences(existing["spec"], p)
                refs.append(await M._event(
                    conn, org_id=org_id, action="fund_build_joined", actor_id=actor["id"],
                    model_id=existing["model"]["id"],
                    detail={"replica_security_id": str(src.entry["id"]), "custodian_code": p.custodian_code,
                            "method": p.method, "top_n": p.top_n, "requested_differs_in": diffs}))
                out = {"joined": True, **existing, "requested_differs_in": diffs}
            else:
                c = construct(src.rows, p)
                rebalance = _rebalance(src.terms, p)
                name = await _model_name(conn, org_id, src.entry, p)
                model, r1 = await M.create_fund_model(
                    conn, org_id=org_id, actor_id=actor["id"], name=name, replica_security_id=src.entry["id"],
                    disclosure=T.disclosure_for(src.entry["ticker"]), description=description)
                refs.append(r1)
                await conn.execute(
                    f"INSERT INTO {T_KEY} (org_id, replica_security_id, custodian_code, method, top_n, model_id, "
                    "created_by) VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6::uuid, $7::uuid)",
                    str(org_id), str(src.entry["id"]), p.custodian_code, p.method, p.top_n, str(model["id"]),
                    str(actor["id"]))
                version = await M._create_version(
                    conn, org_id=org_id, model=model, origin="fund_build", maker_ids=[actor["id"]],
                    created_by=actor["id"], positions=c.positions, origin_ref=src.snapshot["id"],
                    notes=f"Built from the {src.snapshot['as_of'].isoformat()} holdings of {src.entry['ticker']} "
                          f"(terms v{src.terms['version_no']}).",
                    tradability=await U.tradability(conn, org_id))
                spec = await _insert_spec(conn, org_id=org_id, model_id=model["id"], version_id=version["id"],
                                          entry=src.entry, terms_version_id=src.terms["id"],
                                          snapshot_id=src.snapshot["id"], p=p, rebalance=rebalance, c=c,
                                          origin="fund_build", created_by=actor["id"])
                refs.append(await M._event(
                    conn, org_id=org_id, action="create_draft", actor_id=actor["id"], model_id=model["id"],
                    version_id=version["id"],
                    detail={"origin": "fund_build", "spec_id": str(spec["id"]), "snapshot_id": str(src.snapshot["id"]),
                            "terms_version_id": str(src.terms["id"]), **c.counts,
                            "departures": [d["kind"] for d in M._loads(spec["departures"])]}))
                out = {"joined": False, "model": M.model_out(model), "version": M.version_out(version),
                       "spec": spec_out(spec), "construction": construction_out(c), "accounts_using": 0}
    except asyncpg.UniqueViolationError as exc:
        raise M.ModelConflict(f"a model for this fund, custodian and method was just built — reload ({exc.constraint_name})"
                              ) from None
    await M.publish(pool, refs)
    return out


async def spec_detail(conn, org_id, model_id) -> dict:
    model = await M.load_model(conn, org_id, model_id)
    if model["source"] != "fund_replica":
        raise M.ModelNotFound("this model was not built from a fund")
    rows = await conn.fetch(
        f"""SELECT s.*, v.version_no, v.status AS version_status, v.origin AS version_origin
            FROM {T_SPEC} s JOIN {M.T_VER} v ON v.id = s.version_id AND v.system_to IS NULL
            WHERE s.model_id = $1::uuid AND s.valid_to IS NULL AND s.system_to IS NULL
            ORDER BY v.version_no DESC""", model["id"])
    key = await conn.fetchrow(f"SELECT * FROM {T_KEY} WHERE model_id = $1::uuid AND valid_to IS NULL "
                              "AND system_to IS NULL", model["id"])
    events = await conn.fetch(
        f"SELECT action, created_at, version_id, detail FROM {M.T_EVT} WHERE model_id = $1::uuid AND action = ANY($2::text[]) "
        "ORDER BY created_at DESC LIMIT 100", model["id"],
        ["holdings_monitor", "terms_monitor", "terms_review_done", "fund_build_joined"])
    return {
        "model": M.model_out(model),
        "key": {"custodian_code": key["custodian_code"], "method": key["method"], "top_n": key["top_n"]} if key else None,
        "specs": [{**spec_out(dict(r)), "version_no": r["version_no"], "version_status": r["version_status"],
                   "version_origin": r["version_origin"]} for r in rows],
        "monitoring": [{"action": e["action"], "created_at": M._iso(e["created_at"]),
                        "version_id": str(e["version_id"]) if e["version_id"] else None,
                        "detail": M._loads(e["detail"]) or {}} for e in events],
    }


async def monitoring_events(conn, org_id, entry_id) -> list[dict]:
    rows = await conn.fetch(
        f"""SELECT e.action, e.created_at, e.model_id, e.version_id, e.detail, m.name AS model_name
            FROM {M.T_EVT} e JOIN {M.T_MODEL} m ON m.id = e.model_id AND m.system_to IS NULL
            WHERE e.org_id = $1::uuid AND m.replica_security_id = $2::uuid
              AND e.action = ANY($3::text[]) ORDER BY e.created_at DESC LIMIT 100""",
        str(org_id), str(entry_id), ["holdings_monitor", "terms_monitor", "terms_review_done", "fund_build_joined",
                                     "create_draft", "approve"])
    return [{"action": r["action"], "created_at": M._iso(r["created_at"]), "model_id": str(r["model_id"]),
             "model_name": r["model_name"], "version_id": str(r["version_id"]) if r["version_id"] else None,
             "detail": M._loads(r["detail"]) or {}} for r in rows]


# ── monitoring: a new live snapshot ─────────────────────────────────────────
async def monitor_snapshot(pool, *, fund_id: str, source_key: str, snapshot_id: str,
                           include_test_orgs: bool = False) -> dict:
    """Every fund_replica model of this fund (any org but a test org, unless
    ``include_test_orgs`` — services.orgs) against a new LIVE snapshot. Never
    raises: each model's outcome (or error) is reported."""
    from services import orgs as ORGS
    from services.database import reset_rls_context, set_rls_context

    out = {"snapshot_id": str(snapshot_id), "models": [], "errors": []}
    async with H.platform_tx(pool) as conn:
        status = await conn.fetchval("SELECT status FROM portfolio.replica_holdings_snapshots WHERE id = $1::uuid "
                                     "AND system_to IS NULL", str(snapshot_id))
        if status != "live":
            out["skipped"] = f"snapshot is {status or 'missing'}, not live — never used"
            return out
        targets = await conn.fetch(
            f"""SELECT DISTINCT s.org_id, s.model_id FROM {T_SPEC} s
                JOIN portfolio.replica_securities e ON e.id = s.replica_security_id
                  AND e.valid_to IS NULL AND e.system_to IS NULL
                JOIN {M.T_MODEL} m ON m.id = s.model_id AND m.valid_to IS NULL AND m.system_to IS NULL
                  AND m.status <> 'retired'
                WHERE e.global_security_id = $1::uuid AND e.holdings_source_key = $2
                  AND s.valid_to IS NULL AND s.system_to IS NULL
                  AND ($3::boolean OR {ORGS.not_test_org_sql("s.org_id")})
                ORDER BY s.org_id, s.model_id""", str(fund_id), source_key, include_test_orgs)
    for t in targets:
        tokens = set_rls_context(str(t["org_id"]), False)
        try:
            r = await _monitor_model(pool, org_id=str(t["org_id"]), model_id=str(t["model_id"]),
                                     snapshot_id=str(snapshot_id))
            out["models"].append(r)
        except Exception as exc:  # noqa: BLE001 — one model's failure never stops the others
            log.exception("holdings monitor failed for model %s", t["model_id"])
            out["errors"].append({"org_id": str(t["org_id"]), "model_id": str(t["model_id"]), "error": str(exc)[:500]})
        finally:
            reset_rls_context(tokens)
    return out


def _diff_brief(diff: dict) -> dict:
    return {"added": diff.get("added", []), "removed": diff.get("removed", []), "changed": diff.get("changed", []),
            "turnover": diff.get("turnover"), "base_version_id": diff.get("base_version_id")}


async def _monitor_model(pool, *, org_id: str, model_id: str, snapshot_id: str) -> dict:
    version = None
    model = None
    diff_payload = None
    try:
        async with pool.acquire() as conn:
            done = await conn.fetchval(f"SELECT detail->>'outcome' FROM {M.T_EVT} WHERE model_id = $1::uuid AND action = "
                                       "'holdings_monitor' AND detail->>'snapshot_id' = $2", model_id, snapshot_id)
            if done is not None:
                return {"model_id": model_id, "org_id": org_id, "outcome": "already", "first_outcome": done}
            model = await M.load_model(conn, org_id, model_id)
            spec = await latest_spec(conn, model_id)
            live = await M.live_approved(conn, model_id)
            detail: dict = {"snapshot_id": snapshot_id, "spec_id": str(spec["id"]) if spec else None}
            if live is None:
                detail["outcome"] = "no_live_version"
            elif await M.open_version(conn, model_id) is not None:
                detail["outcome"] = "skipped_open_draft"
            else:
                rows = await H.snapshot_rows(conn, snapshot_id)
                p = params_from_spec(spec)
                try:
                    c = construct(rows, p)
                except M.ModelError as exc:
                    c = None
                    detail.update(outcome="not_buildable", reason=str(exc))
                if c is not None:
                    effective = await M._effective_in_memory(conn, c.positions, [], model_id)
                    diff = await M._diff_vs(conn, effective, live["id"])
                    turnover = Decimal(diff["turnover"])
                    threshold = Decimal(spec["turnover_review_threshold"])
                    exceeds = bool(diff["added"] or diff["removed"]) or turnover >= threshold
                    detail.update(turnover=str(turnover), threshold=str(threshold), added=len(diff["added"]),
                                  removed=len(diff["removed"]), changed=len(diff["changed"]),
                                  live_version_id=str(live["id"]))
                    if not exceeds:
                        detail["outcome"] = "within_tolerance"
                    else:
                        entry = await T.load_entry(conn, org_id, spec["replica_security_id"])
                        terms = await T.live_approved(conn, entry["id"])
                        snap = await conn.fetchrow("SELECT as_of FROM portfolio.replica_holdings_snapshots WHERE "
                                                   "id = $1::uuid", snapshot_id)
                        version = await M._create_version(
                            conn, org_id=org_id, model=model, origin="fund_monitor", maker_ids=[], created_by=None,
                            positions=c.positions, origin_ref=snapshot_id,
                            notes=f"Drafted from the {snap['as_of'].isoformat()} holdings of {entry['ticker']}: "
                                  f"turnover {U.pct_text(turnover)} against v{live['version_no']}.",
                            tradability=await U.tradability(conn, org_id))
                        new_spec = await _insert_spec(
                            conn, org_id=org_id, model_id=model_id, version_id=version["id"], entry=entry,
                            terms_version_id=terms["id"] if terms else spec["terms_version_id"],
                            snapshot_id=snapshot_id, p=p, rebalance=_rebalance(terms, p) if terms else (
                                spec["rebalance_frequency"], spec["rebalance_origin"], None),
                            c=c, origin="fund_monitor", created_by=None)
                        diff_payload = _diff_brief(diff)
                        detail.update(outcome="drafted", version_id=str(version["id"]), spec_id=str(new_spec["id"]),
                                      diff=diff_payload)
            ref = await M._event(conn, org_id=org_id, action="holdings_monitor", actor_id=None, model_id=model_id,
                                 version_id=version["id"] if version else None, detail=detail)
    except asyncpg.UniqueViolationError:
        return {"model_id": model_id, "org_id": org_id, "outcome": "already"}
    await M.publish(pool, [ref])
    out = {"model_id": model_id, "org_id": org_id, "outcome": detail["outcome"],
           "turnover": detail.get("turnover")}
    if version is not None:
        sub = await M._submit_system(
            pool, org_id=org_id, model=model, version=version,
            why="the fund's holdings changed beyond the model's review threshold",
            extra_payload={"snapshot_id": snapshot_id, "diff": diff_payload})
        out.update(version_id=str(version["id"]), task_id=sub["task_id"])
    return out


# ── monitoring: a new approved terms version ────────────────────────────────
def changed_critical(prior: dict | None, new: dict) -> list[dict]:
    if prior is None:
        return []
    out = []
    for k in CRITICAL_TERMS:
        a, b = term_value(prior, k), term_value(new, k)
        if a != b:
            out.append({"field_key": k, "before": a, "after": b})
    return out


async def monitor_terms(pool, *, org_id, entry_id, version_id, prior_version_id) -> dict:
    """A review task on each model of the fund naming the changed critical
    fields. No automatic version. Never raises past a per-model failure."""
    out = {"changed": [], "models": [], "errors": []}
    if prior_version_id is None:
        return out
    async with pool.acquire() as conn:
        new = await conn.fetchrow(f"SELECT * FROM {T.T_VER} WHERE id = $1::uuid", str(version_id))
        prior = await conn.fetchrow(f"SELECT * FROM {T.T_VER} WHERE id = $1::uuid", str(prior_version_id))
        changed = changed_critical(dict(prior) if prior else None, dict(new)) if new else []
        model_ids = [r["model_id"] for r in await conn.fetch(
            f"""SELECT DISTINCT s.model_id FROM {T_SPEC} s JOIN {M.T_MODEL} m ON m.id = s.model_id
                  AND m.valid_to IS NULL AND m.system_to IS NULL AND m.status <> 'retired'
                WHERE s.org_id = $1::uuid AND s.replica_security_id = $2::uuid AND s.valid_to IS NULL
                  AND s.system_to IS NULL ORDER BY s.model_id""", str(org_id), str(entry_id))] if changed else []
    out["changed"] = changed
    for mid in model_ids:
        try:
            out["models"].append(await _terms_review(pool, org_id=org_id, model_id=mid, version=dict(new),
                                                     prior_version_id=prior_version_id, changed=changed))
        except Exception as exc:  # noqa: BLE001
            log.exception("terms monitor failed for model %s", mid)
            out["errors"].append({"model_id": str(mid), "error": str(exc)[:500]})
    return out


async def _terms_review(pool, *, org_id, model_id, version: dict, prior_version_id, changed: list[dict]) -> dict:
    try:
        async with pool.acquire() as conn:
            if await conn.fetchval(f"SELECT 1 FROM {M.T_EVT} WHERE model_id = $1::uuid AND action = 'terms_monitor' "
                                   "AND detail->>'terms_version_id' = $2", str(model_id), str(version["id"])):
                return {"model_id": str(model_id), "outcome": "already"}
            model = await M.load_model(conn, org_id, model_id)
            ref = await M._event(conn, org_id=org_id, action="terms_monitor", actor_id=None, model_id=model_id,
                                 detail={"terms_version_id": str(version["id"]),
                                         "prior_terms_version_id": str(prior_version_id),
                                         "terms_version_no": version["version_no"], "changed": changed})
    except asyncpg.UniqueViolationError:
        return {"model_id": str(model_id), "outcome": "already"}
    await M.publish(pool, [ref])
    fields = ", ".join(c["field_key"] for c in changed)
    try:
        task = await HT.open_task(
            pool, org_id=org_id, kind=TERMS_REVIEW_KIND, subject_type=TERMS_REVIEW_SUBJECT, subject_id=model_id,
            maker_id=None, eligible_permission=M.MANAGE,
            title=f"Review fund terms change: {model['name']} ({fields})",
            payload={"model_id": str(model_id), "terms_version_id": str(version["id"]),
                     "terms_version_no": version["version_no"], "changed": changed,
                     "note": "No new version is drafted automatically — decide whether the model needs one."},
            action_path=f"/portfolio/models/{model_id}", actor_kind="system")
        return {"model_id": str(model_id), "outcome": "review_opened", "task_id": task["id"],
                "fields": [c["field_key"] for c in changed]}
    except HT.HumanTaskConflict:
        return {"model_id": str(model_id), "outcome": "review_already_open",
                "fields": [c["field_key"] for c in changed]}


async def _on_terms_review_done(pool, task, outcome, actor_id, note):
    """Log the review on the model (idempotent per task)."""
    org_id = task["org_id"]
    async with pool.acquire() as conn:
        if await conn.fetchval(f"SELECT 1 FROM {M.T_EVT} WHERE model_id = $1::uuid AND action = 'terms_review_done' "
                               "AND detail->>'task_id' = $2", task["subject_id"], task["id"]):
            return None
        ref = await M._event(conn, org_id=org_id, action="terms_review_done", actor_id=actor_id,
                             model_id=task["subject_id"],
                             detail={"task_id": task["id"], "note": note,
                                     "terms_version_id": (task.get("payload") or {}).get("terms_version_id")})
    await M.publish(pool, [ref])
    return {"model_id": task["subject_id"], "event_id": ref.id}


HT.register_kind(TERMS_REVIEW_KIND, label="Fund terms change review", outcomes=("done",), maker_checker=False,
                 on_complete=_on_terms_review_done, system_open=True)


def vocabularies() -> dict:
    def pairs(d):
        return [{"key": k, "label": v} for k, v in d.items()]

    return {"methods": pairs(METHOD_LABELS), "unresolved_policies": pairs(UNRESOLVED_POLICY_LABELS),
            "drop_reasons": pairs(DROP_REASON_LABELS), "departure_kinds": pairs(DEPARTURE_LABELS),
            "monitor_outcomes": pairs(MONITOR_OUTCOME_LABELS), "rebalance_origins": pairs(REBALANCE_ORIGIN_LABELS),
            "custodians": pairs(CC.CUSTODIAN_LABELS)}
