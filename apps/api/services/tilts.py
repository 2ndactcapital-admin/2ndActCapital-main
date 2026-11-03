"""REP10 — tilts: "the index plus my views", with the cost shown.

WHERE A TILT APPLIES (the one place) — rebalance_proposals.target_for
──────────────────────────────────────────────────────────────────────────────
model look-through → the account's exclusions (rep5's apply_exclusions) →
THE APPROVED TILT SET (here) → completion (services.completion) → the engine
(cash floor, lots, gain budget, wash-sale control, lot method). Everything
downstream of the target still applies on top of a tilt.

THE TILT MATH — :func:`apply_tilts` (pure, exact Decimal at numeric(14,10))
──────────────────────────────────────────────────────────────────────────────
``base`` = the account's target after exclusions (cash included).
  security  +d on a security: its weight + d. A security not in the model is
            ADDED at weight d (stated on the line). An underweight larger than
            the position is refused.
  sector    +d on a sector: d spread over the sector's securities pro rata to
            their base weight (largest remainder). Sectors come from the
            model's source holdings file (replica_model_specs.snapshot_id →
            the file's sector column); a model with no source file → refused
            "needs sectors".
  factor    +d on a factor: weight d moves FROM the securities scoring below
            the (weight-free) median score TO those scoring above it, pro rata
            to base weight within each half; at the median nothing moves; −d
            reverses. Scores come ONLY through the security-data layer
            (data type 'factor_score', subject "<security>:<factor>"); unless
            the scored securities cover the stated share of the model's
            security weight, refused "needs factor scores (x% covered)".
RENORMALISATION (stated rule): the net of every delta is taken pro rata from
the UNTILTED securities (those no line names), so the total is exactly the
base total and the cash target is unchanged; with no untilted security left,
cash absorbs the net. Any weight that would go below zero is refused.
LIMITS (org settings, plain reasons when refused): |security delta| ≤
replication.tilts.max_security (default 5%), |sector or factor delta| ≤
replication.tilts.max_sector (10%), active share ≤
replication.tilts.max_active_weight (25%).

WHAT IS ALWAYS SHOWN — :func:`metrics`
──────────────────────────────────────────────────────────────────────────────
active share = ½ Σ |final − model| over every key (cash included); the
largest single-name deviation; deviations by sector.

TRACKING ERROR (ex-ante, HYPOTHETICAL) — :func:`estimate_te`
──────────────────────────────────────────────────────────────────────────────
a = final − model per security (cash has zero variance). Daily simple returns
from eod_price history through the layer ONLY (window
replication.tilts.te_window_days, default 365 calendar days ending today), on
the dates every covered security shares; Σ = sample covariance (n − 1);
TE = √(aᵀ Σ a × 252). A security is covered when it has at least
replication.tilts.te_min_observations (60) returns in the window; the estimate
is made ONLY when covered securities hold at least
replication.tilts.te_min_coverage (95%) of Σ max(model, final) security weight
— otherwise "not estimated — needs prices (x% of weight covered)". An
uncovered security's active weight is listed, never guessed. Recomputed on
every preview (nothing written). Budget: replication.tilts.te_budget (unset =
none); used only when the estimate exists.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_EVEN, Decimal, localcontext

from services import human_tasks as HT
from services import rebalance_engine as E
from services import rep10_common as C

log = logging.getLogger(__name__)

T_SET = "public.tilt_sets"
T_LINE = "public.tilt_lines"
VIEW = "view_replication"
MANAGE = "manage_replication"
APPROVE = "approve_replication"
TASK_KIND = "tilt_set_approval"
SUBJECT = "tilt_set"

KINDS = ("security", "sector", "factor")
KIND_LABELS = {"security": "Security over- / underweight", "sector": "Sector over- / underweight",
               "factor": "Factor tilt"}
STATUS_LABELS = {"draft": "Draft", "pending_approval": "Awaiting approval", "approved": "Approved — in effect",
                 "rejected": "Rejected", "archived": "Archived"}
SETTINGS = {
    "max_security": ("replication.tilts.max_security", Decimal("0.05")),
    "max_sector": ("replication.tilts.max_sector", Decimal("0.10")),
    "max_active_weight": ("replication.tilts.max_active_weight", Decimal("0.25")),
    "te_min_coverage": ("replication.tilts.te_min_coverage", Decimal("0.95")),
    "factor_coverage": ("replication.tilts.factor_coverage", Decimal("0.95")),
}
TE_WINDOW_SETTING = "replication.tilts.te_window_days"
TE_MIN_OBS_SETTING = "replication.tilts.te_min_observations"
TE_BUDGET_SETTING = "replication.tilts.te_budget"
DEFAULT_TE_WINDOW = 365
DEFAULT_TE_MIN_OBS = 60
ANNUALISE = Decimal(252)
UNKNOWN_SECTOR = "unknown sector"
RENORMALISE_RULE = ("the net of every tilt is taken pro rata from the untilted securities, so the total and the cash "
                    "target are unchanged; with none left, cash absorbs it")
TE_METHOD = ("ex-ante: √(aᵀΣa × 252), a = final − model per security, Σ = sample covariance of daily simple returns on "
             "the dates every covered security shares — hypothetical, from eod_price history through the "
             "security-data layer")
HYPOTHETICAL_NOTE = "Projected tracking error is hypothetical — an estimate from past prices, not a promise."


class TiltRefused(C.Rep10Error):
    """A tilt the rules refuse; ``reasons`` are the plain lines."""

    def __init__(self, reasons: list[str]):
        self.reasons = reasons
        super().__init__("; ".join(reasons))


# ═══ Pure math ══════════════════════════════════════════════════════════════
def _spread(keys: list[str], base: dict[str, Decimal], amount: Decimal) -> dict[str, Decimal]:
    """``amount`` (signed) over ``keys`` pro rata to their base weight, exact at numeric(14,10)."""
    if amount == 0 or not keys:
        return {}
    w = [(k, base[k], i) for i, k in enumerate(sorted(keys))]
    if sum((x for _, x, _ in w), C.ZERO) <= 0:
        w = [(k, Decimal(1), i) for i, (k, _, _) in enumerate(w)]
    out = E.allocate(w, abs(amount))
    return {k: (v if amount > 0 else -v) for k, v in out.items()}


def factor_halves(scores: dict[str, Decimal]) -> tuple[list[str], list[str], Decimal | None]:
    """(above, below, median) of the scored keys — median of the scores themselves (weight-free)."""
    if not scores:
        return [], [], None
    vals = sorted(scores.values())
    n = len(vals)
    with localcontext() as ctx:
        ctx.prec = 50
        med = vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2
    return sorted(k for k, v in scores.items() if v > med), sorted(k for k, v in scores.items() if v < med), med


def apply_tilts(base: dict[str, Decimal], lines: list[dict], *, sectors: dict[str, str] | None = None,
                factor_scores: dict[str, dict[str, Decimal]] | None = None, limits: dict | None = None,
                labels: dict[str, str] | None = None) -> dict:
    """``base`` {key | 'cash': weight} after exclusions; ``lines`` [{kind, subject, delta}] →
    {final, model, deltas, notes}. Raises :class:`TiltRefused` with every reason at once."""
    labels = labels or {}
    sectors = sectors or {}
    factor_scores = factor_scores or {}
    lim = {k: v[1] for k, v in SETTINGS.items()} | (limits or {})
    model = {k: C.dec(v) for k, v in base.items()}
    deltas: dict[str, Decimal] = {}
    tilted: set[str] = set()
    notes: list[str] = []
    reasons: list[str] = []
    for ln in lines:
        kind, subj, d = ln["kind"], str(ln["subject"]), C.dec(ln["delta"])
        if kind not in KINDS:
            reasons.append(f"unknown tilt kind {kind!r}")
            continue
        if d is None or d == 0:
            reasons.append(f"{subj}: a tilt needs a non-zero delta")
            continue
        if kind == "security":
            if subj == E.CASH:
                reasons.append("cash is not tilted — it follows the account's cash floor")
                continue
            if abs(d) > lim["max_security"]:
                reasons.append(f"{labels.get(subj, subj)}: a {E.pct(abs(d))} tilt is over the per-security limit of "
                               f"{E.pct(lim['max_security'])}")
            if subj not in model or model[subj] == 0:
                notes.append(f"{labels.get(subj, subj)} is not in the model — added at {E.pct(d)}")
            deltas[subj] = deltas.get(subj, C.ZERO) + d
            tilted.add(subj)
        elif kind == "sector":
            if abs(d) > lim["max_sector"]:
                reasons.append(f"sector {subj}: a {E.pct(abs(d))} tilt is over the per-sector limit of "
                               f"{E.pct(lim['max_sector'])}")
            if not sectors:
                reasons.append(f"sector {subj}: not available — needs sectors (the model has no source holdings file)")
                continue
            members = [k for k, v in model.items() if k != E.CASH and v > 0 and sectors.get(k) == subj]
            if not members:
                reasons.append(f"sector {subj}: no security of the model is in this sector")
                continue
            for k, v in _spread(members, model, d).items():
                deltas[k] = deltas.get(k, C.ZERO) + v
            tilted.update(members)
        else:
            if abs(d) > lim["max_sector"]:
                reasons.append(f"factor {subj}: a {E.pct(abs(d))} tilt is over the limit of {E.pct(lim['max_sector'])}")
            fs = factor_scores.get(subj)
            if fs is None:
                reasons.append(f"factor {subj}: needs factor scores")
                continue
            scored = {k: v for k, v in fs.items() if k in model and model[k] > 0 and k != E.CASH}
            above, below, med = factor_halves(scored)
            if not above or not below:
                reasons.append(f"factor {subj}: the scores do not split the model into an upper and a lower half")
                continue
            up, down = (above, below) if d > 0 else (below, above)
            for k, v in _spread(up, model, abs(d)).items():
                deltas[k] = deltas.get(k, C.ZERO) + v
            for k, v in _spread(down, model, -abs(d)).items():
                deltas[k] = deltas.get(k, C.ZERO) + v
            tilted.update(above + below)
            notes.append(f"factor {subj}: {E.pct(abs(d))} moved from the securities scoring "
                         f"{'below' if d > 0 else 'above'} the median ({med}) to those "
                         f"{'above' if d > 0 else 'below'} it")
    final = dict(model)
    for k, v in deltas.items():
        final[k] = final.get(k, C.ZERO) + v
    net = sum(deltas.values(), C.ZERO)
    if net != 0:
        untilted = [k for k, v in model.items() if k != E.CASH and k not in tilted and v > 0]
        if untilted:
            room = sum((model[k] for k in untilted), C.ZERO) - net
            if room < 0:
                reasons.append(f"the tilts add {E.pct(net)} more than the untilted securities hold "
                               f"({E.pct(room + net)}) — reduce an overweight")
            elif room == 0:
                for k in untilted:
                    final[k] = C.ZERO
            else:
                final.update(E.allocate([(k, model[k], i) for i, k in enumerate(sorted(untilted))], room))
            notes.append(f"the net {E.pct(net)} is taken pro rata from the {len(untilted)} untilted securities")
        else:
            cash = final.get(E.CASH, C.ZERO) - net
            if cash < 0:
                reasons.append(f"the tilts add {E.pct(net)} and no untilted security or cash is left to fund it")
            final[E.CASH] = cash
            notes.append(f"no untilted security is left: cash absorbs the net {E.pct(net)}")
    neg = [k for k, v in final.items() if v < 0]
    for k in neg:
        reasons.append(f"{labels.get(k, k)}: an underweight larger than the position ({E.pct(model.get(k, C.ZERO))})")
    act = active_share(final, model)
    if act > lim["max_active_weight"]:
        reasons.append(f"active share {E.pct(act)} is over the limit of {E.pct(lim['max_active_weight'])}")
    if reasons:
        raise TiltRefused(reasons)
    final = {k: E.qw(v) for k, v in final.items() if v != 0 or k in model}
    return {"final": final, "model": model, "deltas": {k: E.qw(final.get(k, C.ZERO) - model.get(k, C.ZERO))
                                                       for k in set(final) | set(model)}, "notes": notes}


def active_share(final: dict[str, Decimal], model: dict[str, Decimal]) -> Decimal:
    keys = set(final) | set(model)
    tot = sum((abs(final.get(k, C.ZERO) - model.get(k, C.ZERO)) for k in keys), C.ZERO)
    return E.qw(tot / 2)


def metrics(final: dict[str, Decimal], model: dict[str, Decimal], *, labels: dict[str, str] | None = None,
            sectors: dict[str, str] | None = None) -> dict:
    labels, sectors = labels or {}, sectors or {}
    dev = {k: final.get(k, C.ZERO) - model.get(k, C.ZERO) for k in set(final) | set(model) if k != E.CASH}
    top = sorted(dev.items(), key=lambda kv: (-abs(kv[1]), kv[0]))
    by_sector: dict[str, Decimal] = {}
    for k, v in dev.items():
        s = sectors.get(k, UNKNOWN_SECTOR)
        by_sector[s] = by_sector.get(s, C.ZERO) + v
    sec = sorted(by_sector.items(), key=lambda kv: (-abs(kv[1]), kv[0]))
    return {"active_share": str(active_share(final, model)),
            "largest_single_name": ({"key": top[0][0], "label": labels.get(top[0][0], top[0][0]),
                                     "deviation": str(E.qw(top[0][1]))} if top and top[0][1] != 0 else None),
            "single_names": [{"key": k, "label": labels.get(k, k), "deviation": str(E.qw(v))}
                             for k, v in top[:10] if v != 0],
            "sectors": [{"sector": s, "deviation": str(E.qw(v))} for s, v in sec if v != 0],
            "largest_sector": ({"sector": sec[0][0], "deviation": str(E.qw(sec[0][1]))}
                               if sec and sec[0][1] != 0 else None)}


def returns_on_common_dates(series: dict[str, list[tuple[date, Decimal]]]) -> tuple[list[date], dict[str, list]]:
    """Daily simple returns on the dates every series shares (ascending)."""
    if not series:
        return [], {}
    common = None
    for pts in series.values():
        ds = {d for d, _ in pts}
        common = ds if common is None else common & ds
    days = sorted(common or [])
    out = {}
    with localcontext() as ctx:
        ctx.prec = 50
        for k, pts in series.items():
            px = dict(pts)
            out[k] = [px[days[i]] / px[days[i - 1]] - 1 for i in range(1, len(days))]
    return days, out


def covariance(rets: dict[str, list[Decimal]]) -> dict[tuple[str, str], Decimal]:
    keys = sorted(rets)
    n = len(rets[keys[0]]) if keys else 0
    out = {}
    with localcontext() as ctx:
        ctx.prec = 50
        mean = {k: sum(rets[k], C.ZERO) / n for k in keys}
        for i, a in enumerate(keys):
            for b in keys[i:]:
                c = sum(((rets[a][t] - mean[a]) * (rets[b][t] - mean[b]) for t in range(n)), C.ZERO) / (n - 1)
                out[(a, b)] = out[(b, a)] = c
    return out


def te_from(active: dict[str, Decimal], cov: dict[tuple[str, str], Decimal]) -> Decimal:
    keys = sorted(active)
    with localcontext() as ctx:
        ctx.prec = 50
        var = sum((active[a] * active[b] * cov[(a, b)] for a in keys for b in keys), C.ZERO)
        if var < 0:
            var = C.ZERO
        return (var * ANNUALISE).sqrt().quantize(C.Q, rounding=ROUND_HALF_EVEN)


def te_estimate_pure(final: dict[str, Decimal], model: dict[str, Decimal], history: dict[str, list],
                     *, min_obs: int, min_coverage: Decimal) -> dict:
    """``history`` {key: [(date, price)] ascending} for the securities that have any. Pure."""
    secs = sorted(k for k in set(final) | set(model) if k != E.CASH)
    basis = {k: max(final.get(k, C.ZERO), model.get(k, C.ZERO)) for k in secs}
    total = sum(basis.values(), C.ZERO)
    covered = [k for k in secs if len(history.get(k) or []) >= min_obs + 1]
    cov_w = sum((basis[k] for k in covered), C.ZERO)
    share = E.qw(cov_w / total) if total > 0 else C.ZERO
    missing = [k for k in secs if k not in covered]
    out = {"coverage": {"weight_covered": str(share), "threshold": str(min_coverage), "min_observations": min_obs,
                        "covered": len(covered), "securities": len(secs), "missing": missing},
           "hypothetical": True, "method": TE_METHOD}
    if total == 0 or share < min_coverage:
        return {**out, "estimated": False,
                "label": f"{C.NEEDS_PRICES} ({(share * 100).quantize(Decimal('0.01'))}% of weight covered)"}
    days, rets = returns_on_common_dates({k: history[k] for k in covered})
    if len(days) < min_obs + 1:
        return {**out, "estimated": False,
                "label": f"{C.NEEDS_PRICES} (only {max(len(days) - 1, 0)} common daily returns; {min_obs} needed)"}
    active = {k: final.get(k, C.ZERO) - model.get(k, C.ZERO) for k in covered}
    te = te_from(active, covariance(rets))
    return {**out, "estimated": True, "tracking_error": str(te), "observations": len(days) - 1,
            "window": {"start": days[0].isoformat(), "end": days[-1].isoformat()}, "label": C.HYPOTHETICAL,
            "uncovered_active_weight": [{"key": k, "active": str(E.qw(final.get(k, C.ZERO) - model.get(k, C.ZERO)))}
                                        for k in missing if final.get(k, C.ZERO) != model.get(k, C.ZERO)]}


# ═══ Inputs through the layer ═══════════════════════════════════════════════
async def limits_for(conn, org_id) -> dict:
    out = {}
    for k, (key, default) in SETTINGS.items():
        out[k] = await C.setting(conn, org_id, key, default, lo=Decimal("0.0000000001"), hi=Decimal(1))
    return out


async def sectors_for(conn, version_id) -> dict[str, str]:
    """{security id: sector} from the model version's source holdings file (replica_model_specs), else {}."""
    from services import replica_holdings as H
    from services import replica_models as RM
    from services import xray as XR

    spec = await RM.spec_for_version(conn, version_id) if version_id else None
    if not spec:
        return {}
    out = {}
    for h in await H.snapshot_rows(conn, spec["snapshot_id"]):
        if h.get("constituent_global_security_id"):
            out[str(h["constituent_global_security_id"])] = XR.sector_of(C.loads(h.get("raw")))
    return out


async def factor_scores_for(conn, org_id, factors: list[str], keys: list[str], today: date) -> dict:
    """{factor: {key: score}} through the layer (internal use), with the provider and as-of per score."""
    from services import security_data as SD

    out, prov = {}, {}
    for f in sorted(set(factors)):
        subj = [f"{k}:{f}" for k in keys if k != E.CASH]
        got = await SD.get_bulk(conn, org_id, "factor_score", subj, as_of=today, purpose="internal")
        out[f] = {}
        for s, r in got.items():
            if r.found and r.value is not None:
                k = s.split(":", 1)[0]
                out[f][k] = Decimal(str(r.value))
                prov.setdefault(f, []).append({"key": k, "provider": r.provider_key, "as_of": C.iso(r.as_of)})
    return {"scores": out, "provenance": prov}


async def history_for(conn, org_id, keys: list[str], today: date, window_days: int) -> tuple[dict, dict]:
    hist, prov = {}, {}
    start = today - timedelta(days=window_days)
    for k in keys:
        if k == E.CASH:
            continue
        r = await C.longest_history(conn, org_id, k, start, today)
        if r:
            hist[k] = r["points"]
            prov[k] = {"provider": r["provider"], "as_of": C.iso(r["as_of"]), "points": len(r["points"])}
    return hist, prov


async def estimate_te(conn, org_id, final: dict, model: dict, today: date) -> dict:
    window = await C.setting(conn, org_id, TE_WINDOW_SETTING, DEFAULT_TE_WINDOW, lo=30, hi=3660, integer=True)
    min_obs = await C.setting(conn, org_id, TE_MIN_OBS_SETTING, DEFAULT_TE_MIN_OBS, lo=10, hi=2520, integer=True)
    cov = await C.setting(conn, org_id, SETTINGS["te_min_coverage"][0], SETTINGS["te_min_coverage"][1],
                          lo=Decimal("0.0000000001"), hi=Decimal(1))
    keys = sorted(k for k in set(final) | set(model) if k != E.CASH)
    hist, prov = await history_for(conn, org_id, keys, today, window)
    out = te_estimate_pure(final, model, hist, min_obs=min_obs, min_coverage=cov)
    budget = await C.setting(conn, org_id, TE_BUDGET_SETTING, None, lo=Decimal("0.0000000001"), hi=Decimal(1))
    out["window_days"] = window
    out["provenance"] = prov
    out["budget"] = str(budget) if budget is not None else None
    out["over_budget"] = bool(out.get("estimated") and budget is not None
                              and Decimal(out["tracking_error"]) > budget)
    out["note"] = HYPOTHETICAL_NOTE
    return out


# ═══ The account and its base target ════════════════════════════════════════
async def _account_and_base(conn, org_id, account_id) -> tuple[dict, dict, dict, dict]:
    """(account, link, model, base target) — the base is rebalance_proposals.target_for WITHOUT tilts / completion."""
    from services import account_models as AM
    from services import held_away as HA
    from services import rebalance_proposals as RP

    account = await AM.load_account(conn, org_id, C.uid(account_id, "account"))
    HA.refuse(account, "a tilt set", C.Rep10Conflict, path="settings")
    link = await AM.active_link(conn, account["id"])
    if link is None:
        raise C.Rep10Conflict("the account does not follow a model — join one first")
    model = await AM.model_brief(conn, org_id, link["model_id"])
    if not model["live_version_id"]:
        raise C.Rep10Conflict(f"{model['name']} has no approved version")
    base = await RP.target_for(conn, org_id, link, model["live_version_id"], adjust=False)
    return account, link, model, base


def _clean_lines(lines: list[dict]) -> list[dict]:
    out, seen = [], set()
    for i, ln in enumerate(lines or [], start=1):
        kind = str(ln.get("kind") or "")
        subj = str(ln.get("subject") or "").strip()
        if kind not in KINDS:
            raise C.Rep10Error(f"line {i}: kind is one of {list(KINDS)}")
        if not subj:
            raise C.Rep10Error(f"line {i}: name the security, sector or factor")
        if kind == "security":
            subj = C.uid(subj, "security")
        if kind == "factor":
            subj = subj.lower()
        d = ln.get("delta")
        if not isinstance(d, Decimal):
            raise C.Rep10Error(f"line {i}: delta is a decimal fraction (0.01 = one percentage point)")
        if d == 0 or abs(d) >= 1:
            raise C.Rep10Error(f"line {i}: delta is non-zero and between −1 and 1")
        if (kind, subj) in seen:
            raise C.Rep10Error(f"line {i}: {kind} {subj} is named twice")
        seen.add((kind, subj))
        out.append({"kind": kind, "subject": subj, "delta": d, "reason": str(ln.get("reason") or "").strip()})
    return out


async def compute_tilt(conn, org_id, version_id, base_target: dict, labels: dict, lines: list[dict],
                       today: date) -> tuple[dict, dict]:
    """(apply_tilts result, inputs) — the shared step for the preview and for the proposal path."""
    lim = await limits_for(conn, org_id)
    sectors = await sectors_for(conn, version_id) if any(ln["kind"] == "sector" for ln in lines) else {}
    factors = [ln["subject"] for ln in lines if ln["kind"] == "factor"]
    inputs = {"limits": {k: str(v) for k, v in lim.items()}, "renormalise_rule": RENORMALISE_RULE}
    fs = {}
    if factors:
        keys = [k for k in base_target if k != E.CASH]
        got = await factor_scores_for(conn, org_id, factors, keys, today)
        need = await C.setting(conn, org_id, SETTINGS["factor_coverage"][0], SETTINGS["factor_coverage"][1],
                               lo=Decimal("0.0000000001"), hi=Decimal(1))
        sec_w = sum((v for k, v in base_target.items() if k != E.CASH), C.ZERO)
        cover = {}
        for f in factors:
            w = sum((base_target[k] for k in got["scores"].get(f, {}) if k in base_target), C.ZERO)
            share = E.qw(w / sec_w) if sec_w > 0 else C.ZERO
            cover[f] = {"weight_covered": str(share), "threshold": str(need)}
            if share >= need:
                fs[f] = got["scores"][f]
        inputs["factor_coverage"] = cover
        inputs["factor_provenance"] = got["provenance"]
        missing = [f for f in factors if f not in fs]
        if missing:
            raise TiltRefused([f"factor {f}: needs factor scores ({(Decimal(cover[f]['weight_covered']) * 100).quantize(Decimal('0.01'))}% "
                               f"of the model's security weight covered; {(need * 100).quantize(Decimal('0.01'))}% "
                               "needed) — load them through Security data (CSV upload or a vendor)" for f in missing])
    res = apply_tilts(base_target, lines, sectors=sectors, factor_scores=fs, limits=lim, labels=labels)
    inputs["sectors_available"] = bool(sectors)
    res["sectors"] = sectors
    return res, inputs


async def preview(conn, org_id, account_id, lines: list[dict], *, today: date | None = None) -> dict:
    """The live editor: final weights, active share, deviations, TE or "not estimated". Writes nothing."""
    today = today or date.today()
    account, link, model, base = await _account_and_base(conn, org_id, account_id)
    clean = _clean_lines(lines)
    labels = dict(base["labels"])
    for ln in clean:
        if ln["kind"] == "security" and ln["subject"] not in labels:
            labels[ln["subject"]] = await conn.fetchval("SELECT name FROM portfolio.securities_global WHERE id = "
                                                        "$1::uuid", ln["subject"]) or ln["subject"]
    out = {"account": {"id": str(account["id"]), "account_number_masked": account["account_number_masked"]},
           "model": {"id": str(model["id"]), "name": model["name"]}, "hypothetical_note": HYPOTHETICAL_NOTE}
    try:
        res, inputs = await compute_tilt(conn, org_id, model["live_version_id"], base["target"], labels, clean, today)
    except TiltRefused as exc:
        return {**out, "ok": False, "refused": exc.reasons, "lines": C.clean(clean)}
    sectors = res["sectors"] or await sectors_for(conn, model["live_version_id"])
    m = metrics(res["final"], res["model"], labels=labels, sectors=sectors)
    te = await estimate_te(conn, org_id, res["final"], res["model"], today)
    return {**out, "ok": True, "lines": C.clean(clean), "weights": breakdown_out(res, labels), "metrics": m,
            "tracking_error": C.clean(te), "notes": res["notes"], "inputs": inputs}


def breakdown_out(res: dict, labels: dict) -> list[dict]:
    keys = sorted(set(res["final"]) | set(res["model"]), key=lambda k: (k == E.CASH, -res["final"].get(k, C.ZERO), k))
    return [{"key": k, "label": "Cash" if k == E.CASH else labels.get(k, k),
             "model_weight": str(E.qw(res["model"].get(k, C.ZERO))), "tilt": str(res["deltas"].get(k, C.ZERO)),
             "final_weight": str(res["final"].get(k, C.ZERO))} for k in keys]


# ═══ The proposal path (rebalance_proposals.target_for calls this) ═══════════
async def approved_set(conn, account_id) -> dict | None:
    row = await conn.fetchrow(f"SELECT * FROM {T_SET} WHERE account_id = $1::uuid AND status = 'approved' "
                              "AND system_to IS NULL", str(account_id))
    return dict(row) if row else None


async def lines_of(conn, set_id) -> list[dict]:
    return [dict(r) for r in await conn.fetch(f"SELECT * FROM {T_LINE} WHERE tilt_set_id = $1::uuid AND valid_to IS "
                                              "NULL AND system_to IS NULL ORDER BY line_no", str(set_id))]


async def for_target(conn, org_id, account_id, version_id, target: dict, labels: dict, today: date) -> dict | None:
    """The approved set applied to ``target``; None when the account has none. A set that no longer fits (the
    model changed under it) is NOT applied — the proposal says why, and the untilted target stands."""
    s = await approved_set(conn, account_id)
    if s is None:
        return None
    lines = [{"kind": r["kind"], "subject": r["subject"], "delta": C.dec(r["delta"])} for r in await lines_of(conn,
                                                                                                             s["id"])]
    try:
        res, _ = await compute_tilt(conn, org_id, version_id, target, labels, lines, today)
    except TiltRefused as exc:
        return {"set": s, "applied": False, "reasons": exc.reasons}
    return {"set": s, "applied": True, "result": res}


# ═══ Tilt sets (maker-checker) ══════════════════════════════════════════════
def set_out(r: dict, lines: list[dict] | None = None) -> dict:
    return {"id": str(r["id"]), "account_id": str(r["account_id"]), "model_id": str(r["model_id"]),
            "version_no": r["version_no"], "name": r["name"], "reason": r["reason"], "status": r["status"],
            "maker_id": str(r["maker_id"]), "submitted_by": str(r["submitted_by"]) if r["submitted_by"] else None,
            "approved_by": str(r["approved_by"]) if r["approved_by"] else None,
            "approval_task_id": str(r["approval_task_id"]) if r["approval_task_id"] else None,
            "decided_at": C.iso(r["decided_at"]), "decision_note": r["decision_note"],
            "archived_reason": r["archived_reason"], "metrics": C.loads(r["metrics"]),
            "created_at": C.iso(r["created_at"]),
            "lines": [{"id": str(x["id"]), "line_no": x["line_no"], "kind": x["kind"], "subject": x["subject"],
                       "subject_label": x["subject_label"], "delta": str(x["delta"]), "reason": x["reason"]}
                      for x in (lines or [])]}


async def load_set(conn, org_id, set_id) -> dict:
    row = await conn.fetchrow(f"SELECT * FROM {T_SET} WHERE id = $1::uuid AND org_id = $2::uuid AND system_to IS NULL",
                              C.uid(set_id, "tilt set"), str(org_id))
    if row is None:
        raise C.Rep10NotFound("tilt set not found")
    return dict(row)


async def sets_for(conn, org_id, account_id) -> list[dict]:
    rows = await conn.fetch(f"SELECT * FROM {T_SET} WHERE org_id = $1::uuid AND account_id = $2::uuid "
                            "AND system_to IS NULL ORDER BY version_no DESC", str(org_id), C.uid(account_id, "account"))
    return [set_out(dict(r), await lines_of(conn, r["id"])) for r in rows]


def actions_for(s: dict, *, can_write: bool, can_approve: bool, user_id) -> list[str]:
    a = []
    if s["status"] == "draft" and can_write:
        a += ["edit", "submit", "discard"]
    if s["status"] == "pending_approval" and can_approve and str(user_id) not in (s["maker_id"], s.get("submitted_by")):
        a += ["approve", "reject"]
    if s["status"] == "approved" and can_write:
        a.append("archive")
    return a


async def _labels_for(conn, lines: list[dict]) -> dict[str, str]:
    out = {}
    for ln in lines:
        if ln["kind"] == "security":
            out[ln["subject"]] = await conn.fetchval("SELECT name FROM portfolio.securities_global WHERE id = $1::uuid",
                                                     ln["subject"]) or ln["subject"]
    return out


async def create(pool, *, org_id, actor_id, account_id, name: str, reason: str, lines: list[dict],
                 today: date | None = None) -> dict:
    """A new DRAFT version, its lines validated against the rules first (nothing written on a refusal)."""
    today = today or date.today()
    if not (name or "").strip() or not (reason or "").strip():
        raise C.Rep10Error("a tilt set needs a name and a reason")
    clean = _clean_lines(lines)
    if not clean:
        raise C.Rep10Error("a tilt set needs at least one line")
    async with pool.acquire() as conn:
        actor = await C.human(conn, actor_id, org_id)
        account, link, model, base = await _account_and_base(conn, org_id, account_id)
        labels = {**base["labels"], **await _labels_for(conn, clean)}
        await compute_tilt(conn, org_id, model["live_version_id"], base["target"], labels, clean, today)  # refuses
        if await conn.fetchval(f"SELECT 1 FROM {T_SET} WHERE account_id = $1::uuid AND status IN ('draft', "
                               "'pending_approval') AND system_to IS NULL", str(account["id"])):
            raise C.Rep10Conflict("the account already has an open tilt set — finish or discard it first")
        async with conn.transaction():
            version = int(await conn.fetchval(f"SELECT COALESCE(max(version_no), 0) + 1 FROM {T_SET} WHERE "
                                              "account_id = $1::uuid AND system_to IS NULL", str(account["id"])))
            row = await conn.fetchrow(
                f"INSERT INTO {T_SET} (org_id, account_id, model_id, version_no, name, reason, maker_id) "
                "VALUES ($1::uuid, $2::uuid, $3::uuid, $4, $5, $6, $7::uuid) RETURNING *",
                str(org_id), str(account["id"]), str(model["id"]), version, name.strip(), reason.strip(),
                str(actor["id"]))
            await _insert_lines(conn, org_id, row["id"], clean, labels, actor["id"])
            await C.audit(conn, org_id, actor["id"], "tilt_set_created", SUBJECT, row["id"],
                          {"account_id": str(account["id"]), "version_no": version, "lines": len(clean)})
        return set_out(dict(row), await lines_of(conn, row["id"]))


async def _insert_lines(conn, org_id, set_id, clean: list[dict], labels: dict, actor_id) -> None:
    for i, ln in enumerate(clean, start=1):
        await conn.execute(
            f"INSERT INTO {T_LINE} (org_id, tilt_set_id, line_no, kind, subject, subject_label, delta, reason, "
            "created_by) VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, $7, $8, $9::uuid)",
            str(org_id), str(set_id), i, ln["kind"], ln["subject"], labels.get(ln["subject"]), ln["delta"],
            ln["reason"] or "—", str(actor_id))


async def replace_lines(pool, *, org_id, actor_id, set_id, lines: list[dict], today: date | None = None) -> dict:
    """A draft's lines, replaced (valid axis: the live lines are closed, the new ones inserted)."""
    today = today or date.today()
    clean = _clean_lines(lines)
    if not clean:
        raise C.Rep10Error("a tilt set needs at least one line")
    async with pool.acquire() as conn:
        actor = await C.human(conn, actor_id, org_id)
        s = await load_set(conn, org_id, set_id)
        if s["status"] != "draft":
            raise C.Rep10Conflict(f"a {STATUS_LABELS[s['status']].lower()} tilt set is not edited — make a new version")
        _, _, model, base = await _account_and_base(conn, org_id, s["account_id"])
        labels = {**base["labels"], **await _labels_for(conn, clean)}
        await compute_tilt(conn, org_id, model["live_version_id"], base["target"], labels, clean, today)
        async with conn.transaction():
            await conn.execute(f"UPDATE {T_LINE} SET valid_to = now() WHERE tilt_set_id = $1::uuid AND valid_to IS "
                               "NULL AND system_to IS NULL", str(s["id"]))
            await _insert_lines(conn, org_id, s["id"], clean, labels, actor["id"])
            await C.audit(conn, org_id, actor["id"], "tilt_lines_replaced", SUBJECT, s["id"], {"lines": len(clean)})
        return set_out(s, await lines_of(conn, s["id"]))


async def submit(pool, *, org_id, actor_id, set_id, today: date | None = None) -> dict:
    """Draft → awaiting approval: re-validated, its metrics (and TE when estimable) frozen on the row, the task opened."""
    today = today or date.today()
    async with pool.acquire() as conn:
        actor = await C.human(conn, actor_id, org_id)
        s = await load_set(conn, org_id, set_id)
        if s["status"] != "draft":
            raise C.Rep10Conflict("only a draft tilt set is submitted")
        lines = [{"kind": r["kind"], "subject": r["subject"], "delta": C.dec(r["delta"])}
                 for r in await lines_of(conn, s["id"])]
        p = await preview(conn, org_id, s["account_id"], lines, today=today)
        if not p["ok"]:
            raise TiltRefused(p["refused"])
        te = p["tracking_error"]
        if te.get("over_budget"):
            raise TiltRefused([f"estimated tracking error {te['tracking_error']} is over the budget {te['budget']} "
                               "(hypothetical)"])
        account = p["account"]
    task = await HT.open_task(pool, org_id=org_id, kind=TASK_KIND, subject_type=SUBJECT, subject_id=s["id"],
                              maker_id=actor["id"], eligible_permission=APPROVE,
                              title=f"Approve tilt set v{s['version_no']}: {account['account_number_masked']}",
                              payload={"account": account["account_number_masked"], "name": s["name"],
                                       "active_share": p["metrics"]["active_share"]},
                              action_path=f"/portfolio/replication/tilts?account={s['account_id']}")
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                row = await C.update(conn, T_SET, s["id"], {
                    "status": "pending_approval", "submitted_by": actor["id"],
                    "submitted_at": datetime.now(timezone.utc), "approval_task_id": uuid.UUID(task["id"]),
                    "metrics": C.dumps({"metrics": p["metrics"], "tracking_error": te, "weights": p["weights"]})})
                await C.audit(conn, org_id, actor["id"], "tilt_set_submitted", SUBJECT, s["id"], {"task_id": task["id"]})
    except Exception:
        await C.withdraw_task(pool, task["id"], "the tilt set could not be submitted")
        raise
    return {**set_out(row), "task": task}


async def decide(pool, *, org_id, actor_id, set_id, outcome: str, note: str | None = None) -> dict:
    if outcome not in ("approve", "reject"):
        raise C.Rep10Error("outcome is approve or reject")
    if outcome == "reject" and not (note or "").strip():
        raise C.Rep10Error("a rejection needs a note saying why")
    async with pool.acquire() as conn:
        actor = await C.human(conn, actor_id, org_id)
        s = await load_set(conn, org_id, set_id)
    if s["status"] != "pending_approval":
        raise C.Rep10Conflict(f"the tilt set is {STATUS_LABELS[s['status']].lower()}, not awaiting approval")
    if str(actor["id"]) in (str(s["maker_id"]), str(s["submitted_by"])):
        raise C.MakerCheckerViolation(f"maker-checker: you made this tilt set, so a different holder of {APPROVE} "
                                      f"must {outcome} it")
    await C.complete(pool, org_id=org_id, task_id=s["approval_task_id"], actor_id=actor["id"], outcome=outcome,
                     note=note)
    async with pool.acquire() as conn:
        return set_out(await load_set(conn, org_id, s["id"]), await lines_of(conn, s["id"]))


async def _apply(pool, task: dict, outcome: str, actor_id, note) -> dict | None:
    org_id = task["org_id"]
    async with pool.acquire() as conn:
        async with conn.transaction():
            s = await conn.fetchrow(f"SELECT * FROM {T_SET} WHERE id = $1::uuid AND org_id = $2::uuid AND "
                                    "system_to IS NULL FOR UPDATE", task["subject_id"], str(org_id))
            if s is None or s["status"] != "pending_approval":
                return None
            checker = uuid.UUID(str(actor_id))
            if checker in (s["maker_id"], s["submitted_by"]):
                raise C.MakerCheckerViolation("maker-checker: the maker of a tilt set cannot approve it")
            if outcome == "approve":
                prev = await conn.fetchrow(f"SELECT id FROM {T_SET} WHERE account_id = $1 AND status = 'approved' "
                                           "AND system_to IS NULL FOR UPDATE", s["account_id"])
                if prev:
                    await C.update(conn, T_SET, prev["id"], {"status": "archived",
                                                              "archived_reason": f"superseded by version "
                                                                                 f"{s['version_no']}"})
                fields = {"status": "approved", "approved_by": checker, "decided_at": datetime.now(timezone.utc),
                          "decision_note": note}
            else:
                fields = {"status": "rejected", "approved_by": checker, "decided_at": datetime.now(timezone.utc),
                          "decision_note": note}
            row = await C.update(conn, T_SET, s["id"], fields)
            await C.audit(conn, org_id, checker, f"tilt_set_{fields['status']}", SUBJECT, s["id"],
                          {"task_id": task["id"], "note": note})
    return set_out(row)


async def _on_complete(pool, task, outcome, actor_id, note):
    return await _apply(pool, task, outcome, actor_id, note)


HT.register_kind(TASK_KIND, label="Tilt set approval", outcomes=("approve", "reject"), maker_checker=True,
                 on_complete=_on_complete)


async def archive_set(pool, *, org_id, actor_id, set_id, reason: str) -> dict:
    """Discard a draft or retire an approved set (the untilted target applies from the next proposal)."""
    if not (reason or "").strip():
        raise C.Rep10Error("say why the tilt set is archived")
    async with pool.acquire() as conn:
        actor = await C.human(conn, actor_id, org_id)
        s = await load_set(conn, org_id, set_id)
        if s["status"] not in ("draft", "approved"):
            raise C.Rep10Conflict(f"a {STATUS_LABELS[s['status']].lower()} tilt set is not archived")
        async with conn.transaction():
            row = await C.update(conn, T_SET, s["id"], {"status": "archived", "archived_reason": reason.strip()})
            await C.audit(conn, org_id, actor["id"], "tilt_set_archived", SUBJECT, s["id"], {"reason": reason})
        return set_out(row, await lines_of(conn, s["id"]))


def vocabularies() -> dict:
    return {"tilt_kinds": [{"key": k, "label": v} for k, v in KIND_LABELS.items()],
            "tilt_statuses": [{"key": k, "label": v} for k, v in STATUS_LABELS.items()],
            "renormalise_rule": RENORMALISE_RULE, "te_method": TE_METHOD, "hypothetical_note": HYPOTHETICAL_NOTE,
            "delta_unit": "fraction of the account (0.01 = one percentage point)"}
