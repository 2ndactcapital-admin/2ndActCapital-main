"""S28 — the AI drift monitor: a standing alarm over S27's decision log (public.ai_decision_log).

For every AI task (``task_type``) and org, compare a RECENT window with the task's own BASELINE and raise a
governed alert when it drifts:

  * cost per call     recent median  > baseline median × (1 + X %)          default X = 50
  * latency           recent p95     > baseline p95    × (1 + X %)          default X = 50
  * fallback rate     recent − baseline > N percentage points               default N = 10
  * failure rate      recent − baseline > N points                          default N = 5
  * bypass rate       recent − baseline > N points (LiteLLM bypassed)       default N = 10
  * effort downgrade  recent − baseline > N points                          default N = 10
  * model change      a model_used in the recent window the baseline never had — always an alert
  * model mismatch    model_used ≠ model_requested without fallback_used — the router said one thing and another ran
  * silence           a baseline but zero calls in a window it normally has calls (per task; default OFF)

Each rule has a minimum sample (recent AND baseline) below which it says "not enough data" and never alarms. The
rule for choosing it (docs/S28_DISCOVERY.md 1c): one event in the recent window must never cross a threshold on its
own — a rate rule of N points needs ⌈100 / N⌉ recent calls, a p95 needs 20 (with fewer, p95 is the maximum, so one
slow call moves it), a median needs 5; a baseline needs 20 calls (the smallest sample whose nearest-rank p95 is not its maximum).

DEFINITIONS (exact, so a hand-computed fixture equals the numbers here):
  * median — the middle value of the sorted sample; for an even count, the mean of the two middle values (Decimal);
  * p95    — NEAREST RANK: the k-th smallest value, k = ⌈0.95 · n⌉ (1-based). With n = 20, k = 19;
  * cost   — over rows whose cost_usd is not NULL (a failed call writes NULL cost); Decimal throughout;
  * latency — over SUCCESSFUL rows whose latency_ms is not NULL (a refusal is logged as 1 ms);
  * fallback / failure / bypass rate — events × 100 / calls, in points;
  * effort downgrade — over calls with effort_requested set: effort_used NULL or a lower level than requested;
  * model mismatch — fallback_used false and model_used ≠ model_requested, EXCEPT a date-pinned resolution of the
    requested alias (``gpt-5-mini`` → ``gpt-5-mini-2025-08-07``: the proxy reporting the dated version of the very
    model it was asked for). A real version change of that kind is still caught by the model-change rule.

WINDOWS (time is injected — ``now`` is a parameter everywhere, never the wall clock inside a rule):
  recent   = [now − recent_hours, now)                         default 24 h
  baseline = [recent start − baseline_days, recent start)      default 28 days (excludes the recent window)
  7-day    = [now − 7 d, now)                                  a view on the page; no rule reads it

ALERTS (Task 4) — ONE open public.ai_drift_alerts row per (org, task, rule) (a partial unique index enforces it):
opened on the first drifting evaluation, refreshed with the latest numbers on every later one, CLOSED (never
deleted) once the rule has been back inside its threshold continuously for a full recent window. Every transition
is an append-only public.ai_drift_alert_events row. People are told through the SAME member_todos path held
platform-job runs use (services.workflow_todos.create_ai_drift_alert_todos): the org's manage_org_settings holders,
the platform org's, and the Super Admin who owns the job.

THE MONITOR CALLS NO MODEL and makes no network call: it reads ai_decision_log and writes its own tables and todos.
It never imports a provider client.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Iterable

RULES: tuple[str, ...] = ("cost_per_call", "latency_p95", "fallback_rate", "failure_rate", "bypass_rate",
                          "effort_downgrade_rate", "model_change", "model_mismatch", "silence")
RULE_LABELS = {
    "cost_per_call": "Cost per call", "latency_p95": "Latency (p95)", "fallback_rate": "Fallback rate",
    "failure_rate": "Failure rate", "bypass_rate": "LiteLLM bypass rate", "effort_downgrade_rate": "Effort downgrades",
    "model_change": "Model change", "model_mismatch": "Model mismatch", "silence": "Silence",
}
#: the unit each rule's threshold is in (shown beside the editor); None = the rule has no threshold
RULE_UNITS = {"cost_per_call": "percent", "latency_p95": "percent", "fallback_rate": "points",
              "failure_rate": "points", "bypass_rate": "points", "effort_downgrade_rate": "points",
              "model_change": None, "model_mismatch": None, "silence": "calls"}
UNIT_LABELS = {"percent": "% above baseline", "points": "percentage points above baseline",
               "calls": "expected calls in the window (fires only when at least this many were expected)"}
RATE_RULES = {"fallback_rate": "fallback", "failure_rate": "failure", "bypass_rate": "bypass",
              "effort_downgrade_rate": "effort"}
NO_THRESHOLD = ("model_change", "model_mismatch")

#: the platform defaults (docs/s28_part1.sql seeds the same values as version 1)
DEFAULT_THRESHOLDS: dict[str, dict] = {
    "cost_per_call": {"threshold": Decimal(50), "min_recent": 5, "min_baseline": 20, "enabled": True},
    "latency_p95": {"threshold": Decimal(50), "min_recent": 20, "min_baseline": 20, "enabled": True},
    "fallback_rate": {"threshold": Decimal(10), "min_recent": 10, "min_baseline": 20, "enabled": True},
    "failure_rate": {"threshold": Decimal(5), "min_recent": 20, "min_baseline": 20, "enabled": True},
    "bypass_rate": {"threshold": Decimal(10), "min_recent": 10, "min_baseline": 20, "enabled": True},
    "effort_downgrade_rate": {"threshold": Decimal(10), "min_recent": 10, "min_baseline": 20, "enabled": True},
    "model_change": {"threshold": None, "min_recent": 1, "min_baseline": 20, "enabled": True},
    "model_mismatch": {"threshold": None, "min_recent": 1, "min_baseline": 0, "enabled": True},
    "silence": {"threshold": Decimal(5), "min_recent": 1, "min_baseline": 20, "enabled": False},
}

STATUSES = {"ok": "OK", "drift": "Drift", "insufficient": "Not enough data", "disabled": "Off"}
ALERT_STATUSES = {"open": "Open", "closed": "Closed"}
EFFORT_ORDER = ("low", "medium", "high")   # services.extraction.EFFORT_LEVELS, by budget (verify asserts equality)

RECENT_HOURS = 24
BASELINE_DAYS = 28
VIEW_DAYS = 7
SERIES_DAYS = 90
PAGE_PATH = "/admin/ai-drift"

#: the job's verify-only knobs (production leaves it empty): now / org_ids / task_types / include_test_orgs
JOB_KWARGS: dict = {}

_DATE_SUFFIX = re.compile(r"\d{4}-?\d{2}-?\d{2}")


class DriftError(RuntimeError):
    pass


class ThresholdError(ValueError):
    """An invalid threshold edit (the route answers 422 with this text)."""


# ═══ pure metrics ═══════════════════════════════════════════════════════════
def median(values: Iterable) -> Decimal | None:
    s = sorted(Decimal(v) for v in values)
    n = len(s)
    if n == 0:
        return None
    if n % 2:
        return s[n // 2]
    return (s[n // 2 - 1] + s[n // 2]) / 2


def p95(values: Iterable) -> Decimal | None:
    """Nearest rank: the k-th smallest, k = ⌈0.95 n⌉ (integer arithmetic — no float)."""
    s = sorted(Decimal(v) for v in values)
    n = len(s)
    if n == 0:
        return None
    k = (95 * n + 99) // 100
    return s[k - 1]


def rate_points(events: int, n: int) -> Decimal | None:
    if n <= 0:
        return None
    return Decimal(events * 100) / Decimal(n)


def is_mismatch(row: dict) -> bool:
    if row.get("fallback_used"):
        return False
    req, used = row.get("model_requested") or "", row.get("model_used") or ""
    if used == req:
        return False
    if req and used.startswith(req + "-") and _DATE_SUFFIX.fullmatch(used[len(req) + 1:]):
        return False   # a date-pinned resolution of the requested alias — the same model, reported precisely
    return True


def is_effort_downgrade(row: dict) -> bool:
    req = row.get("effort_requested")
    if not req:
        return False
    used = row.get("effort_used")
    if not used:
        return True
    try:
        return EFFORT_ORDER.index(used) < EFFORT_ORDER.index(req)
    except ValueError:
        return used != req   # an unknown level that is not the requested one: not what was asked for


def window_stats(rows: list[dict]) -> dict:
    """Every metric for one window of ai_decision_log rows (any mix of orgs — the caller scopes the rows)."""
    costs = [r["cost_usd"] for r in rows if r.get("cost_usd") is not None]
    # latency over SUCCESSFUL calls: a refused / failed call is logged with latency_ms = 1 (the router's policy
    # refusals) or the time to an error — neither is the provider's answer time
    lats = [r["latency_ms"] for r in rows if r.get("latency_ms") is not None and r.get("success") is True]
    n = len(rows)
    eff = [r for r in rows if r.get("effort_requested")]
    fb = sum(1 for r in rows if r.get("fallback_used"))
    fail = sum(1 for r in rows if r.get("success") is False)
    byp = sum(1 for r in rows if r.get("litellm_bypassed"))
    down = sum(1 for r in eff if is_effort_downgrade(r))
    models: dict[str, dict] = {}
    for r in sorted(rows, key=lambda x: x["created_at"]):
        m = r.get("model_used") or ""
        e = models.setdefault(m, {"first_seen": r["created_at"], "calls": 0})
        e["calls"] += 1
    mism = [r for r in rows if is_mismatch(r)]
    return {
        "calls": n,
        "cost": {"n": len(costs), "median": median(costs), "p95": p95(costs),
                 "total": sum((Decimal(c) for c in costs), Decimal(0))},
        "latency": {"n": len(lats), "median": median(lats), "p95": p95(lats)},
        "fallback": {"n": n, "events": fb, "rate": rate_points(fb, n)},
        "failure": {"n": n, "events": fail, "rate": rate_points(fail, n)},
        "bypass": {"n": n, "events": byp, "rate": rate_points(byp, n)},
        "effort": {"n": len(eff), "events": down, "rate": rate_points(down, len(eff))},
        "models": models,
        "mismatch": {"events": len(mism),
                     "pairs": sorted({f"{r.get('model_requested')} → {r.get('model_used')}" for r in mism})},
    }


# ═══ pure rules ═════════════════════════════════════════════════════════════
@dataclass
class Threshold:
    rule_key: str
    threshold: Decimal | None
    min_recent: int
    min_baseline: int
    enabled: bool
    id: str | None = None
    version: int | None = None
    task_type: str | None = None   # None = the platform default

    def as_dict(self) -> dict:
        return {"id": self.id, "rule_key": self.rule_key, "threshold": self.threshold, "min_recent": self.min_recent,
                "min_baseline": self.min_baseline, "enabled": self.enabled, "version": self.version,
                "scope": "task" if self.task_type else "platform", "task_type": self.task_type}


@dataclass
class Finding:
    rule_key: str
    status: str            # ok | drift | insufficient | disabled
    sentence: str
    numbers: dict = field(default_factory=dict)
    threshold: Threshold | None = None


def _fmt(v, unit: str = "") -> str:
    if v is None:
        return "—"
    if unit == "usd":
        return f"${Decimal(v):.6f}"
    if unit == "ms":
        return f"{Decimal(v):.0f} ms"
    if unit == "pts":
        return f"{Decimal(v):.1f}%"
    return str(v)


def evaluate_rules(baseline: dict, recent: dict, thresholds: dict[str, Threshold], *,
                   recent_hours: int = RECENT_HOURS, baseline_days: int = BASELINE_DAYS) -> list[Finding]:
    """Every rule, in RULES order, for one (org, task): ok / drift / insufficient (never alarms) / disabled."""
    out: list[Finding] = []
    for key in RULES:
        th = thresholds.get(key)
        if th is None:
            raise DriftError(f"no threshold for rule {key!r}")
        if not th.enabled:
            out.append(Finding(key, "disabled", f"{RULE_LABELS[key]} is switched off.", {}, th))
            continue
        out.append(_rule(key, th, baseline, recent, recent_hours, baseline_days))
    return out


def _insufficient(key, th, rn, bn, what="calls") -> Finding:
    return Finding(key, "insufficient",
                   f"Not enough data: {rn} recent and {bn} baseline {what} (needs {th.min_recent} and "
                   f"{th.min_baseline}).", {"recent_n": rn, "baseline_n": bn, "min_recent": th.min_recent,
                                            "min_baseline": th.min_baseline}, th)


def _rule(key: str, th: Threshold, b: dict, r: dict, recent_hours: int, baseline_days: int) -> Finding:
    if key in ("cost_per_call", "latency_p95"):
        metric, stat, unit = (("cost", "median", "usd") if key == "cost_per_call" else ("latency", "p95", "ms"))
        rn, bn = r[metric]["n"], b[metric]["n"]
        if rn < max(1, th.min_recent) or bn < max(1, th.min_baseline):   # a comparison needs both sides
            return _insufficient(key, th, rn, bn)
        rv, bv = r[metric][stat], b[metric][stat]
        limit = bv * (1 + th.threshold / 100)
        nums = {"recent": rv, "baseline": bv, "limit": limit, "recent_n": rn, "baseline_n": bn,
                "threshold": th.threshold, "unit": RULE_UNITS[key], "stat": stat}
        drift = rv > limit
        word = "median" if stat == "median" else "p95"
        s = (f"{RULE_LABELS[key]}: recent {word} {_fmt(rv, unit)} (n={rn}) vs baseline {_fmt(bv, unit)} (n={bn}); "
             f"limit +{th.threshold}% = {_fmt(limit, unit)}.")
        return Finding(key, "drift" if drift else "ok", s, nums, th)
    if key in RATE_RULES:
        m = RATE_RULES[key]
        rn, bn = r[m]["n"], b[m]["n"]
        what = "calls with an effort requested" if m == "effort" else "calls"
        if rn < max(1, th.min_recent) or bn < max(1, th.min_baseline):
            return _insufficient(key, th, rn, bn, what)
        rv, bv = r[m]["rate"], b[m]["rate"]
        rise = rv - bv
        nums = {"recent": rv, "baseline": bv, "rise": rise, "recent_events": r[m]["events"], "recent_n": rn,
                "baseline_events": b[m]["events"], "baseline_n": bn, "threshold": th.threshold, "unit": "points"}
        s = (f"{RULE_LABELS[key]}: recent {_fmt(rv, 'pts')} ({r[m]['events']}/{rn}) vs baseline {_fmt(bv, 'pts')} "
             f"({b[m]['events']}/{bn}); rise {rise:+.1f} points, limit {th.threshold} points.")
        return Finding(key, "drift" if rise > th.threshold else "ok", s, nums, th)
    if key == "model_change":
        rn, bn = r["calls"], b["calls"]
        if rn < th.min_recent or bn < th.min_baseline:
            return _insufficient(key, th, rn, bn)
        new = sorted(m for m in r["models"] if m not in b["models"])
        nums = {"new_models": [{"model": m, "first_seen": r["models"][m]["first_seen"],
                                "calls": r["models"][m]["calls"]} for m in new],
                "baseline_models": sorted(b["models"]), "recent_models": sorted(r["models"]),
                "recent_n": rn, "baseline_n": bn}
        if new:
            return Finding(key, "drift", f"Model change: {', '.join(new)} ran in the recent window and never in the "
                                         f"baseline ({', '.join(sorted(b['models'])) or 'none'}).", nums, th)
        return Finding(key, "ok", f"Model change: every recent model was in the baseline "
                                  f"({', '.join(sorted(r['models']))}).", nums, th)
    if key == "model_mismatch":
        rn, bn = r["calls"], b["calls"]
        if rn < th.min_recent or bn < th.min_baseline:
            return _insufficient(key, th, rn, bn)
        ev = r["mismatch"]["events"]
        nums = {"recent_events": ev, "recent_n": rn, "pairs": r["mismatch"]["pairs"]}
        if ev:
            return Finding(key, "drift", f"Model mismatch: {ev} of {rn} recent calls ran a different model than the "
                                         f"router requested, with no fallback ({'; '.join(r['mismatch']['pairs'])}).",
                           nums, th)
        return Finding(key, "ok", f"Model mismatch: none in {rn} recent calls.", nums, th)
    if key == "silence":
        bn, rn = b["calls"], r["calls"]
        if bn < th.min_baseline:
            return _insufficient(key, th, rn, bn)
        expected = Decimal(bn) * Decimal(recent_hours) / Decimal(baseline_days * 24)
        nums = {"recent_n": rn, "baseline_n": bn, "expected": expected, "threshold": th.threshold, "unit": "calls"}
        if rn == 0 and expected >= th.threshold:
            return Finding(key, "drift", f"Silence: no calls in the recent window; the baseline averages "
                                         f"{expected:.2f} per window (floor {th.threshold}).", nums, th)
        return Finding(key, "ok", f"Silence: {rn} recent calls (baseline averages {expected:.2f} per window).",
                       nums, th)
    raise DriftError(f"unknown rule {key!r}")


def worst(statuses: Iterable[str]) -> str:
    s = set(statuses)
    if "drift" in s:
        return "drift"
    if "ok" in s:
        return "ok"
    return "insufficient"


# ═══ windows + reads ════════════════════════════════════════════════════════
@dataclass(frozen=True)
class Windows:
    now: datetime
    recent_start: datetime
    baseline_start: datetime
    view_start: datetime
    recent_hours: int
    baseline_days: int

    def as_dict(self) -> dict:
        return {"now": self.now, "recent": [self.recent_start, self.now],
                "baseline": [self.baseline_start, self.recent_start], "view_7d": [self.view_start, self.now],
                "recent_hours": self.recent_hours, "baseline_days": self.baseline_days}


def windows(now: datetime, *, recent_hours: int = RECENT_HOURS, baseline_days: int = BASELINE_DAYS) -> Windows:
    if now.tzinfo is None:
        raise DriftError("now must be timezone-aware")
    rs = now - timedelta(hours=recent_hours)
    return Windows(now, rs, rs - timedelta(days=baseline_days), now - timedelta(days=VIEW_DAYS), recent_hours,
                   baseline_days)


_COLS = ("org_id, task_type, model_requested, model_used, fallback_used, cost_usd, latency_ms, success, "
         "effort_requested, effort_used, litellm_bypassed, created_at")


async def read_window(conn, *, start: datetime, end: datetime, org_ids=None, task_types=None,
                      include_test_orgs: bool = False) -> list[dict]:
    """ONE read: every ai_decision_log row in [start, end), optionally for some orgs / tasks. Test orgs are skipped
    unless a verify asks (services.orgs). The caller's RLS context decides what is visible."""
    from services.orgs import not_test_org_sql

    sql = (f"SELECT {_COLS} FROM public.ai_decision_log WHERE created_at >= $1 AND created_at < $2 "
           "AND ($3::uuid[] IS NULL OR org_id = ANY($3::uuid[])) AND ($4::text[] IS NULL OR task_type = ANY($4::text[]))")
    if not include_test_orgs:
        sql += " AND " + not_test_org_sql("org_id")
    rows = await conn.fetch(sql + " ORDER BY created_at, id", start, end,
                            [str(o) for o in org_ids] if org_ids is not None else None,
                            list(task_types) if task_types is not None else None)
    return [dict(r) for r in rows]


def group(rows: list[dict], keys=("org_id", "task_type")) -> dict[tuple, list[dict]]:
    out: dict[tuple, list[dict]] = {}
    for r in rows:
        out.setdefault(tuple(r[k] for k in keys), []).append(r)
    return out


# ═══ thresholds (versioned, Super Admin writes) ═════════════════════════════
def _th_from_row(r) -> Threshold:
    return Threshold(r["rule_key"], r["threshold"], int(r["min_recent"]), int(r["min_baseline"]), bool(r["enabled"]),
                     str(r["id"]), int(r["version"]), r["task_type"])


async def live_thresholds(conn) -> list[Threshold]:
    return [_th_from_row(r) for r in await conn.fetch(
        "SELECT id, task_type, rule_key, threshold, min_recent, min_baseline, enabled, version FROM "
        "public.ai_drift_thresholds WHERE valid_to IS NULL ORDER BY rule_key, task_type NULLS FIRST")]


def resolve_thresholds(live: list[Threshold], task_type: str) -> dict[str, Threshold]:
    """A per-task override wins over the platform default. A rule with no platform default row is a loud error —
    the monitor never invents a threshold."""
    default = {t.rule_key: t for t in live if t.task_type is None}
    override = {t.rule_key: t for t in live if t.task_type == task_type}
    missing = [k for k in RULES if k not in default and k not in override]
    if missing:
        raise DriftError(f"no live threshold for {missing} (docs/s28_part1.sql seeds the platform defaults)")
    return {k: override.get(k) or default[k] for k in RULES}


async def threshold_history(conn, *, task_type: str | None = None) -> list[dict]:
    rows = await conn.fetch(
        "SELECT t.id, t.task_type, t.rule_key, t.threshold, t.min_recent, t.min_baseline, t.enabled, t.version, "
        "t.valid_from, t.valid_to, t.changed_by, t.change_note, u.full_name AS changed_by_name FROM "
        "public.ai_drift_thresholds t LEFT JOIN public.users u ON u.id = t.changed_by WHERE ($1::text IS NULL OR "
        "t.task_type IS NULL OR t.task_type = $1) ORDER BY t.rule_key, t.task_type NULLS FIRST, t.version DESC",
        task_type)
    return [dict(r) for r in rows]


def validate_threshold(rule_key: str, threshold, min_recent: int, min_baseline: int) -> Decimal | None:
    if rule_key not in RULES:
        raise ThresholdError(f"rule_key must be one of {', '.join(RULES)}")
    if rule_key in NO_THRESHOLD:
        if threshold is not None:
            raise ThresholdError(f"{RULE_LABELS[rule_key]} has no threshold (it is always an alert)")
        val = None
    else:
        if threshold is None:
            raise ThresholdError(f"{RULE_LABELS[rule_key]} needs a threshold")
        try:
            val = Decimal(str(threshold))
        except Exception:   # noqa: BLE001
            raise ThresholdError("threshold must be a number") from None
        if not val.is_finite() or val < 0:
            raise ThresholdError("threshold must be zero or more")
    if not isinstance(min_recent, int) or isinstance(min_recent, bool) or min_recent < 1:
        raise ThresholdError("min_recent must be a whole number of at least 1")
    if not isinstance(min_baseline, int) or isinstance(min_baseline, bool) or min_baseline < 0:
        raise ThresholdError("min_baseline must be a whole number of at least 0")
    return val


async def set_threshold(conn, *, rule_key: str, task_type: str | None, threshold, min_recent: int, min_baseline: int,
                        enabled: bool, actor_id, actor_org_id, note: str | None) -> dict:
    """A Super Admin's edit = a NEW VERSION (Rule 3, valid time): the live row for (rule, task) is closed and version
    + 1 inserted, in one transaction, audited. ``task_type`` None edits the platform default; a task name creates or
    edits that task's override. Caller holds a Super Admin (platform) context; the route gates first."""
    val = validate_threshold(rule_key, threshold, min_recent, min_baseline)
    task_type = (task_type or "").strip() or None
    async with conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
                           f"ai_drift_threshold:{rule_key}:{task_type or ''}")
        live = await conn.fetchrow(
            "SELECT id, version, threshold, min_recent, min_baseline, enabled FROM public.ai_drift_thresholds WHERE "
            "rule_key = $1 AND task_type IS NOT DISTINCT FROM $2 AND valid_to IS NULL FOR UPDATE", rule_key, task_type)
        last = await conn.fetchval(
            "SELECT max(version) FROM public.ai_drift_thresholds WHERE rule_key = $1 AND task_type IS NOT DISTINCT "
            "FROM $2", rule_key, task_type)
        if live is not None:
            hit = await conn.fetchval("UPDATE public.ai_drift_thresholds SET valid_to = clock_timestamp() WHERE id = $1 "
                                      "AND valid_to IS NULL RETURNING id", live["id"])
            if hit is None:   # zero rows: an RLS-blocked write and a raced edit look the same — name neither
                raise DriftError("the live threshold was not closed (no row updated) — nothing was changed")
        row = await conn.fetchrow(
            "INSERT INTO public.ai_drift_thresholds (task_type, rule_key, threshold, min_recent, min_baseline, enabled, "
            "version, valid_from, changed_by, change_note) VALUES ($1, $2, $3, $4, $5, $6, $7, clock_timestamp(), "
            "$8::uuid, $9) RETURNING id, version, valid_from", task_type, rule_key, val, min_recent, min_baseline,
            bool(enabled), int(last or 0) + 1, str(actor_id) if actor_id else None, (note or "")[:2000] or None)
        before = ({k: live[k] for k in ("version", "threshold", "min_recent", "min_baseline", "enabled")}
                  if live is not None else None)
        await conn.execute(
            "INSERT INTO public.audit_log (org_id, user_id, action, resource_type, resource_id, payload) VALUES "
            "($1::uuid, $2::uuid, 'ai_drift.threshold_changed', 'ai_drift_threshold', $3::uuid, $4::jsonb)",
            str(actor_org_id), str(actor_id) if actor_id else None, str(row["id"]),
            json.dumps(jsonable({"rule_key": rule_key, "task_type": task_type, "before": before,
                                 "after": {"version": row["version"], "threshold": val, "min_recent": min_recent,
                                           "min_baseline": min_baseline, "enabled": bool(enabled)},
                                 "note": note})))
    return {"id": str(row["id"]), "version": int(row["version"]), "rule_key": rule_key, "task_type": task_type,
            "threshold": val, "min_recent": min_recent, "min_baseline": min_baseline, "enabled": bool(enabled),
            "valid_from": row["valid_from"], "previous_version": before["version"] if before else None}


# ═══ evaluation ═════════════════════════════════════════════════════════════
@dataclass
class PairResult:
    org_id: str
    task_type: str
    baseline: dict
    recent: dict
    findings: list[Finding]

    @property
    def status(self) -> str:
        return worst(f.status for f in self.findings)


async def evaluate(conn, *, now: datetime, org_ids=None, task_types=None, include_test_orgs: bool = False,
                   recent_hours: int = RECENT_HOURS, baseline_days: int = BASELINE_DAYS,
                   live: list[Threshold] | None = None) -> tuple[Windows, list[PairResult]]:
    """Every (org, task) seen in either window: two reads (baseline, recent) + the live thresholds. Pure after that."""
    w = windows(now, recent_hours=recent_hours, baseline_days=baseline_days)
    base = await read_window(conn, start=w.baseline_start, end=w.recent_start, org_ids=org_ids, task_types=task_types,
                             include_test_orgs=include_test_orgs)
    rec = await read_window(conn, start=w.recent_start, end=w.now, org_ids=org_ids, task_types=task_types,
                            include_test_orgs=include_test_orgs)
    live = live if live is not None else await live_thresholds(conn)
    gb, gr = group(base), group(rec)
    out = []
    for key in sorted(set(gb) | set(gr), key=lambda k: (k[1], str(k[0]))):
        org, task = key
        b, r = window_stats(gb.get(key, [])), window_stats(gr.get(key, []))
        out.append(PairResult(str(org), task, b, r, evaluate_rules(b, r, resolve_thresholds(live, task),
                                                                   recent_hours=recent_hours,
                                                                   baseline_days=baseline_days)))
    return w, out


# ═══ alerts ═════════════════════════════════════════════════════════════════
def _alert_numbers(p: PairResult, f: Finding, w: Windows) -> dict:
    return jsonable({"rule": f.rule_key, "status": f.status, "sentence": f.sentence, **f.numbers,
                     "threshold_version": f.threshold.version if f.threshold else None,
                     "threshold_scope": ("task" if f.threshold and f.threshold.task_type else "platform"),
                     "baseline_calls": p.baseline["calls"], "recent_calls": p.recent["calls"],
                     "windows": w.as_dict()})


async def _event(conn, alert_id, org_id, event: str, at: datetime, numbers: dict, note: str | None = None) -> None:
    await conn.execute("INSERT INTO public.ai_drift_alert_events (alert_id, org_id, event, at, numbers, note) VALUES "
                       "($1::uuid, $2::uuid, $3, $4, $5::jsonb, $6)", str(alert_id), str(org_id), event, at,
                       json.dumps(numbers), note)


def link_for(task_type: str) -> str:
    from urllib.parse import quote

    return f"{PAGE_PATH}?task={quote(task_type)}"


async def apply_findings(conn, results: list[PairResult], w: Windows, *, platform_org_id, owner_ids=(),
                         org_names: dict | None = None) -> dict:
    """Open / refresh / recover / close alerts for every finding. Caller holds a Super Admin (platform) context on
    ``conn`` and on the app pool's context vars (the todo recipients are read through the pool)."""
    from services import workflow_todos as WT

    org_names = org_names or {}
    counts = {"opened": 0, "updated": 0, "recovering": 0, "closed": 0, "todos": 0}
    touched: list[str] = []
    recovery = timedelta(hours=w.recent_hours)
    for p in results:
        for f in p.findings:
            async with conn.transaction():
                cur = await conn.fetchrow(
                    "SELECT id, ok_since, opened_at FROM public.ai_drift_alerts WHERE org_id = $1::uuid AND task_type = "
                    "$2 AND rule_key = $3 AND status = 'open' FOR UPDATE", p.org_id, p.task_type, f.rule_key)
                nums = _alert_numbers(p, f, w)
                title = f"AI drift — {p.task_type}: {RULE_LABELS[f.rule_key]}"
                detail = f"{org_names.get(p.org_id, p.org_id)} · {f.sentence}"
                if f.status == "drift":
                    if cur is None:
                        aid = await conn.fetchval(
                            "INSERT INTO public.ai_drift_alerts (org_id, task_type, rule_key, status, opened_at, "
                            "last_evaluated_at, last_drift_at, evaluations, detail, numbers, threshold_id, link) VALUES "
                            "($1::uuid, $2, $3, 'open', $4, $4, $4, 1, $5, $6::jsonb, $7::uuid, $8) "
                            "ON CONFLICT (org_id, task_type, rule_key) WHERE status = 'open' DO NOTHING RETURNING id",
                            p.org_id, p.task_type, f.rule_key, w.now, detail, json.dumps(nums),
                            f.threshold.id if f.threshold else None, link_for(p.task_type))
                        if aid is None:
                            raise DriftError(f"an open alert for {p.task_type}/{f.rule_key} appeared concurrently")
                        await _event(conn, aid, p.org_id, "opened", w.now, nums)
                        counts["opened"] += 1
                    else:
                        aid = cur["id"]
                        await conn.execute(
                            "UPDATE public.ai_drift_alerts SET last_evaluated_at = $2, last_drift_at = $2, evaluations = "
                            "evaluations + 1, ok_since = NULL, detail = $3, numbers = $4::jsonb, threshold_id = $5::uuid "
                            "WHERE id = $1", aid, w.now, detail, json.dumps(nums),
                            f.threshold.id if f.threshold else None)
                        await _event(conn, aid, p.org_id, "updated", w.now, nums)
                        counts["updated"] += 1
                    ids = await WT.create_ai_drift_alert_todos(
                        conn, alert_org_id=p.org_id, alert_id=aid, title=title, detail=detail,
                        link=link_for(p.task_type), platform_org_id=platform_org_id, owner_ids=owner_ids)
                    counts["todos"] += len(ids)
                    touched.append(str(aid))
                elif cur is not None:
                    aid = cur["id"]
                    if f.status == "ok":
                        if cur["ok_since"] is None:
                            await conn.execute("UPDATE public.ai_drift_alerts SET ok_since = $2, last_evaluated_at = $2, "
                                               "evaluations = evaluations + 1, numbers = $3::jsonb WHERE id = $1", aid,
                                               w.now, json.dumps(nums))
                            await _event(conn, aid, p.org_id, "recovering", w.now, nums,
                                         "back inside its threshold; closes after a full recent window")
                            counts["recovering"] += 1
                        elif w.now - cur["ok_since"] >= recovery:
                            await _close(conn, aid, p.org_id, w.now, nums,
                                         f"back inside its threshold since {cur['ok_since'].isoformat()} — a full "
                                         f"{w.recent_hours} h recent window")
                            counts["closed"] += 1
                        else:
                            await conn.execute("UPDATE public.ai_drift_alerts SET last_evaluated_at = $2, evaluations = "
                                               "evaluations + 1, numbers = $3::jsonb WHERE id = $1", aid, w.now,
                                               json.dumps(nums))
                    elif f.status == "disabled":
                        await _close(conn, aid, p.org_id, w.now, nums, "the rule was switched off")
                        counts["closed"] += 1
                    else:   # insufficient: recovery is not proven — the alert stays open and the clock restarts
                        await conn.execute("UPDATE public.ai_drift_alerts SET last_evaluated_at = $2, evaluations = "
                                           "evaluations + 1, ok_since = NULL WHERE id = $1", aid, w.now)
                    touched.append(str(aid))
    return {**counts, "alert_ids": touched}


async def _close(conn, alert_id, org_id, at: datetime, numbers: dict, reason: str) -> None:
    from services import workflow_todos as WT

    await conn.execute("UPDATE public.ai_drift_alerts SET status = 'closed', closed_at = $2, close_reason = $3, "
                       "last_evaluated_at = $2, evaluations = evaluations + 1, numbers = $4::jsonb WHERE id = $1 AND "
                       "status = 'open'", alert_id, at, reason, json.dumps(numbers))
    await _event(conn, alert_id, org_id, "closed", at, numbers, reason)
    await WT.close_ai_drift_alert_todos(conn, alert_id=alert_id, note=reason)


async def org_names(conn, org_ids) -> dict:
    ids = sorted({str(o) for o in org_ids})
    if not ids:
        return {}
    return {str(r["id"]): r["name"] for r in await conn.fetch(
        "SELECT id, name FROM public.organizations WHERE id = ANY($1::uuid[])", ids)}


def findings_report(results: list[PairResult], names: dict | None = None) -> list[dict]:
    names = names or {}
    return [{"org_id": p.org_id, "org": names.get(p.org_id, p.org_id), "task_type": p.task_type,
             "rule": f.rule_key, "status": f.status, "sentence": f.sentence}
            for p in results for f in p.findings]


# ═══ the platform job ═══════════════════════════════════════════════════════
ACTION_KEY = "ai.drift_monitor"
JOB_KEY = "ai_drift_monitor"


async def run_job(pool, *, org_id, user_id, run_context=None, workflow_run_id=None) -> dict:
    """The Service Task: evaluate every task and org, open / refresh / close alerts. Runs only in a platform-job org
    (security_master_jobs.PLATFORM_JOB_ORG_IDS), under a Super Admin context. Any failure RAISES → the engine HOLDS
    the run and alerts the Super Admin (the held-run path every platform job uses)."""
    from services import security_master_jobs as J
    from services.database import reset_rls_context, set_rls_context

    from services.orgs import is_test_org

    J._assert_platform(org_id)
    kw = dict(JOB_KWARGS)
    now = kw.pop("now", None) or datetime.now(timezone.utc)
    tokens = set_rls_context(str(org_id), True)
    try:
        async with pool.acquire() as conn:
            org_ids, include_test = kw.get("org_ids"), bool(kw.get("include_test_orgs"))
            if await is_test_org(conn, org_id):
                # A FIXTURE platform org's run (another verify firing every job in J.JOBS) never reads or alerts on a
                # real org: unless its verify named orgs, it evaluates its own org only — and only test orgs at all.
                org_ids, include_test = (org_ids if org_ids is not None else [str(org_id)]), True
                real = await conn.fetchval(
                    "SELECT count(*) FROM public.organizations WHERE id = ANY($1::uuid[]) AND NOT is_test",
                    [str(o) for o in org_ids])
                if real:
                    raise DriftError("a test platform org's run may evaluate test orgs only")
            w, results = await evaluate(conn, now=now, org_ids=org_ids, task_types=kw.get("task_types"),
                                        include_test_orgs=include_test)
            names = await org_names(conn, {p.org_id for p in results})
            applied = await apply_findings(conn, results, w, platform_org_id=org_id,
                                           owner_ids=[user_id] if user_id else [], org_names=names)
    except Exception as exc:
        raise J.PlatformJobError(f"{JOB_KEY}: {type(exc).__name__}: {str(exc)[:500]}") from None
    finally:
        reset_rls_context(tokens)
    by_status: dict[str, int] = {}
    for p in results:
        for f in p.findings:
            by_status[f.status] = by_status.get(f.status, 0) + 1
    summary = {"job": JOB_KEY, "now": now.isoformat(), "pairs": len(results), "findings_by_status": by_status,
               **{k: v for k, v in applied.items() if k != "alert_ids"}, "model_calls": 0,
               "workflow_run_id": str(workflow_run_id) if workflow_run_id else None}
    return {"data": summary, "text": (f"AI drift monitor: {len(results)} task/org pairs; {by_status.get('drift', 0)} "
                                      f"drifting; {applied['opened']} opened, {applied['closed']} closed")}


async def _handler(*, pool=None, user_id=None, org_id=None, run_context=None, workflow_run_id=None, **_):
    return await run_job(pool, org_id=org_id, user_id=user_id, run_context=run_context,
                         workflow_run_id=workflow_run_id)


def register_actions() -> None:
    from services.action_registry import REGISTRY, AssistantAction
    from services.security_master_jobs import REQUIRED_PERMISSION

    REGISTRY.register(AssistantAction(
        key=ACTION_KEY, module="ai_drift",
        description="Platform job: compare every AI task's recent decision-log behaviour with its baseline and open "
                    "or close drift alerts. Reads ai_decision_log only; calls no model.",
        access_type="write", required_permission=REQUIRED_PERMISSION, tier=2, reversible=False,
        render_target="inline", handler=_handler, params_schema={"type": "object", "properties": {}, "required": []},
        workflow_invocable=True))


# ═══ the page's reads (one query per section) ═══════════════════════════════
def daily_series(rows: list[dict], *, end: datetime, days: int = SERIES_DAYS) -> list[dict]:
    """One point per UTC day for the last ``days`` days (oldest first), each with the window metrics of that day."""
    by_day: dict = {}
    for r in rows:
        by_day.setdefault(r["created_at"].astimezone(timezone.utc).date(), []).append(r)
    last = end.astimezone(timezone.utc).date()
    out = []
    for i in range(days - 1, -1, -1):
        d = last - timedelta(days=i)
        s = window_stats(by_day.get(d, []))
        out.append({"day": d.isoformat(), "calls": s["calls"], "cost_median": s["cost"]["median"],
                    "cost_total": s["cost"]["total"], "latency_p95": s["latency"]["p95"],
                    "fallback_rate": s["fallback"]["rate"], "failure_rate": s["failure"]["rate"],
                    "bypass_rate": s["bypass"]["rate"], "models": sorted(s["models"])})
    return out


def _summary(s: dict) -> dict:
    return {"calls": s["calls"], "cost_median": s["cost"]["median"], "cost_p95": s["cost"]["p95"],
            "cost_total": s["cost"]["total"], "cost_n": s["cost"]["n"], "latency_median": s["latency"]["median"],
            "latency_p95": s["latency"]["p95"], "latency_n": s["latency"]["n"],
            "fallback_rate": s["fallback"]["rate"], "failure_rate": s["failure"]["rate"],
            "bypass_rate": s["bypass"]["rate"], "effort_downgrade_rate": s["effort"]["rate"],
            "effort_n": s["effort"]["n"],
            "models": [{"model": m, "first_seen": v["first_seen"], "calls": v["calls"]}
                       for m, v in sorted(s["models"].items(), key=lambda kv: kv[1]["first_seen"])]}


async def overview(conn, *, now: datetime, org_ids=None, include_test_orgs: bool = False) -> dict:
    """The page's task table: one row per task — 24 h / 7 d / baseline metrics over the caller's scope (``org_ids``
    None = every org, a Super Admin; [own org] for an org admin), each rule's status (the worst over the scope's
    orgs) and open alerts. Reads: baseline window, 7-day window, thresholds, open alerts — one query each."""
    w = windows(now)
    base = await read_window(conn, start=w.baseline_start, end=w.recent_start, org_ids=org_ids,
                             include_test_orgs=include_test_orgs)
    view = await read_window(conn, start=w.view_start, end=w.now, org_ids=org_ids, include_test_orgs=include_test_orgs)
    live = await live_thresholds(conn)
    alerts = await conn.fetch(
        "SELECT task_type, rule_key, count(*) AS n FROM public.ai_drift_alerts WHERE status = 'open' AND "
        "($1::uuid[] IS NULL OR org_id = ANY($1::uuid[])) GROUP BY 1, 2",
        [str(o) for o in org_ids] if org_ids is not None else None)
    open_by_task: dict[str, dict] = {}
    for a in alerts:
        open_by_task.setdefault(a["task_type"], {})[a["rule_key"]] = int(a["n"])
    rec = [r for r in view if r["created_at"] >= w.recent_start]
    gb, gr, gv = group(base, ("task_type",)), group(rec, ("task_type",)), group(view, ("task_type",))
    pb, pr = group(base), group(rec)
    rows = []
    for (task,) in sorted(set(gb) | set(gv)):
        statuses: dict[str, list[str]] = {k: [] for k in RULES}
        th = resolve_thresholds(live, task)
        for key in {k for k in set(pb) | set(pr) if k[1] == task}:
            fs = evaluate_rules(window_stats(pb.get(key, [])), window_stats(pr.get(key, [])), th)
            for f in fs:
                statuses[f.rule_key].append(f.status)
        rule_status = {k: (worst(v) if any(s != "disabled" for s in v) else "disabled") if v else "insufficient"
                       for k, v in statuses.items()}
        rows.append({"task_type": task, "recent_24h": _summary(window_stats(gr.get((task,), []))),
                     "view_7d": _summary(window_stats(gv.get((task,), []))),
                     "baseline": _summary(window_stats(gb.get((task,), []))),
                     "rule_status": rule_status, "status": worst(rule_status.values()),
                     "open_alerts": sum(open_by_task.get(task, {}).values()),
                     "open_alerts_by_rule": open_by_task.get(task, {}), "link": link_for(task),
                     "overrides": sorted(t.rule_key for t in live if t.task_type == task)})
    return {"windows": w.as_dict(), "tasks": rows}


async def task_detail(conn, *, now: datetime, task_type: str, org_ids=None, include_test_orgs: bool = False) -> dict:
    """A task's detail: 90 daily points (one read), its per-org evaluation (two reads), its alerts with their
    events (two reads), its thresholds + history (two reads)."""
    end = now
    start = (now.astimezone(timezone.utc) - timedelta(days=SERIES_DAYS - 1)).replace(hour=0, minute=0, second=0,
                                                                                    microsecond=0)
    rows = await read_window(conn, start=start, end=end, org_ids=org_ids, task_types=[task_type],
                             include_test_orgs=include_test_orgs)
    live = await live_thresholds(conn)
    w, results = await evaluate(conn, now=now, org_ids=org_ids, task_types=[task_type], live=live,
                                include_test_orgs=include_test_orgs)
    names = await org_names(conn, {p.org_id for p in results})
    alerts = [dict(r) for r in await conn.fetch(
        "SELECT a.id, a.org_id, o.name AS org_name, a.rule_key, a.status, a.opened_at, a.last_evaluated_at, "
        "a.last_drift_at, a.evaluations, a.ok_since, a.closed_at, a.close_reason, a.detail, a.numbers, a.link "
        "FROM public.ai_drift_alerts a LEFT JOIN public.organizations o ON o.id = a.org_id WHERE a.task_type = $1 AND "
        "($2::uuid[] IS NULL OR a.org_id = ANY($2::uuid[])) ORDER BY a.status DESC, a.opened_at DESC LIMIT 200",
        task_type, [str(o) for o in org_ids] if org_ids is not None else None)]
    ev = {}
    if alerts:
        for e in await conn.fetch("SELECT alert_id, event, at, note FROM public.ai_drift_alert_events WHERE alert_id = "
                                  "ANY($1::uuid[]) ORDER BY at", [str(a["id"]) for a in alerts]):
            ev.setdefault(str(e["alert_id"]), []).append({"event": e["event"], "at": e["at"], "note": e["note"]})
    for a in alerts:
        a["numbers"] = json.loads(a["numbers"]) if isinstance(a["numbers"], str) else a["numbers"]
        a["events"] = ev.get(str(a["id"]), [])
    resolved = resolve_thresholds(live, task_type)
    return {"task_type": task_type, "windows": w.as_dict(), "series": daily_series(rows, end=end),
            "orgs": [{"org_id": p.org_id, "org": names.get(p.org_id, p.org_id), "status": p.status,
                      "recent": _summary(p.recent), "baseline": _summary(p.baseline),
                      "findings": [{"rule": f.rule_key, "status": f.status, "sentence": f.sentence} for f in p.findings]}
                     for p in results],
            "alerts": alerts, "thresholds": [resolved[k].as_dict() for k in RULES],
            "history": await threshold_history(conn, task_type=task_type)}


# ═══ JSON ═══════════════════════════════════════════════════════════════════
def jsonable(v: Any):
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, dict):
        return {str(k): jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple, set)):
        return [jsonable(x) for x in v]
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return v
