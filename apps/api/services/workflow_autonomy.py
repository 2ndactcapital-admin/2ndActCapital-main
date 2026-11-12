"""S29B — Workflow Manager Wave 4: scheduled / event-triggered workflows may run WRITE steps on their own.

The rules, in the order the engine applies them to each Service Task of a trigger-started run (``decide_step``):

  1. A run a PERSON started (no trigger) is attended — unchanged.
  2. A step that runs no handler (not ``workflow_invocable``) or only reads — unchanged.
  3. Effective Tier 1 — suspends for a checker who passes services.sod against the trigger owner (S30, unchanged).
  4. A kill switch (platform: Super Admin; org: org admin) is on → the step SUSPENDS for a person.
  5. A platform job (the security-master / prices / drift / EDGAR / LiteLLM system workflows) in the Hollisworks
     platform org, owned by a Super Admin → runs as before S29B, under its own on / off switch (BUGFIX4).
  6. The verb's autonomy class is ``never`` → SUSPENDS for a person (and validation refuses the trigger up front).
  7. The run has no named owner → SUSPENDS (no maker, no unattended write).
  8. The workflow version is in SHADOW → the write is RECORDED (verb, arguments, target, what it would produce) and
     NOT executed.
  9. The version is opted in AND the org switch ``workflows.autonomy`` is on → limits → the verb runs as the trigger
     owner's DELEGATE, claimed once per run-step by an idempotency key. Over a limit, or after a failure, the run
     HOLDS and alerts. Otherwise → SUSPENDS for a person.

Money movement, custody, authority, role / permission changes and trade submission are ``never``: they need counsel
(cash stage C) and are not enabled here.

All governed settings live in ``public.workflow_autonomy_settings`` (versioned, valid-time, maker-checker through
services.sod). Tightening (switch off, kill switch on, lower a limit, more shadow runs) applies at once; loosening
(autonomy on, a version on, raise a limit, fewer shadow runs) is a draft a DIFFERENT person decides.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from services import sod

PLATFORM_ORG_ID = "bb347258-8f28-4f49-8cc9-e29ccad82884"

# ── Task 2: autonomy classes (stored on the verb: AssistantAction.autonomy_class) ──────────────────────────────────
NEVER = "never"
ELIGIBLE = "eligible"
ELIGIBLE_WITH_CAP = "eligible_with_cap"
CLASSES = (NEVER, ELIGIBLE, ELIGIBLE_WITH_CAP)

#: The eligibility list (docs/S29B_DISCOVERY.md 1a). ``writes``: what the verb writes. The four flags are why a verb is
#: ``never``. ``platform_job``: a system workflow governed by its own Super Admin switch in the platform org.
VERB_AUTONOMY: dict[str, dict] = {
    "ai.drift_monitor": {"class": ELIGIBLE, "platform_job": True,
                         "writes": "ai_drift_alerts (opens / closes drift alerts) + alert to-dos"},
    "crm.draft_note": {"class": ELIGIBLE, "writes": "a CRM note draft (crm notes)"},
    "edgar.launch_pipeline_job": {"class": ELIGIBLE, "platform_job": True,
                                  "writes": "edgar_pipeline_runs; launches a Render job that fetches public SEC filings"},
    "entity.link_ownership": {"class": NEVER, "custody": True,
                              "writes": "entity_relationships (ownership percentages)"},
    "litellm.reload_model_cost_map": {"class": ELIGIBLE, "platform_job": True,
                                      "writes": "the LiteLLM proxy's in-memory cost map (no database row)"},
    "propose": {"class": ELIGIBLE, "writes": "agent_proposals (a draft for human review — nothing executes)"},
    "security_data.nightly": {"class": ELIGIBLE, "platform_job": True, "writes": "security data refresh tables"},
    "security_data.prices": {"class": ELIGIBLE, "platform_job": True, "writes": "end-of-day prices"},
    "security_master.attributes": {"class": ELIGIBLE, "platform_job": True, "writes": "security master attributes"},
    "security_master.mutual_funds": {"class": ELIGIBLE, "platform_job": True, "writes": "mutual-fund share classes"},
    "security_master.openfigi_new": {"class": ELIGIBLE, "platform_job": True, "writes": "FIGI mappings"},
    "security_master.sec13f": {"class": ELIGIBLE, "platform_job": True, "writes": "the 13(f) list + CUSIP maps"},
    "security_master.universe": {"class": ELIGIBLE, "platform_job": True, "writes": "the listed universe"},
    "spv.record_transaction": {"class": NEVER, "money": True,
                               "writes": "spv_transactions (capital calls, distributions, fees) — money"},
    "spv.subscribe": {"class": NEVER, "money": True, "contacts": True,
                      "writes": "a co-investment subscription (a capital commitment)"},
    "spv_carry.propose_from_realization": {"class": ELIGIBLE,
                                           "writes": "a carry run proposal (spv carry runs, status proposed)"},
    # CASH04 — the monthly / on-maturity waterfall: DRAFTS only (a cash proposal in status draft, no task, plus one
    # review task per household). It never submits, approves or routes — those two verbs are declared NEVER below and
    # are deliberately NOT registered as actions, so no workflow and no agent can reach them (the custody cliff).
    "cash.maturity_waterfall": {"class": ELIGIBLE, "platform_job": True, "draft_only": True,
                                "writes": "draft cash proposals (rebalance_proposals kind cash, status draft) + one "
                                          "review task per household — never submits or routes"},
    "cash.proposal_submit": {"class": NEVER, "trades": True,
                             "writes": "submits a cash proposal for approval (a trade list) — a person only"},
    "cash.proposal_route": {"class": NEVER, "trades": True,
                            "writes": "routes an approved cash proposal to execution instructions — a person only"},
    # CASH07 — payment instructions (money between a family's own accounts). Money movement is excluded from autonomy
    # until counsel signs off (Joe): ALL FOUR are never. The assistant may at most DRAFT one while a person is present
    # — that verb is declared here (and in sod.VERB_DUTIES as 'propose') but NOT registered as an action in stage A,
    # so no workflow and no agent can reach any of them. Submit / approve / send are a person's only (the custody cliff).
    "cash.payment_draft": {"class": NEVER, "money": True,
                           "writes": "a payment instruction DRAFT (payment_instructions, status draft) — never submits"},
    "cash.payment_submit": {"class": NEVER, "money": True,
                            "writes": "submits a payment instruction for approval — a person only"},
    "cash.payment_approve": {"class": NEVER, "money": True,
                             "writes": "approves a payment instruction (the S30 floor) — a different person only"},
    "cash.payment_send": {"class": NEVER, "money": True,
                          "writes": "sends a payment instruction through its adapter — a person only"},
    # cpsim04 — reading the (simulated) custodian's transfer status and applying it to the payment: a person only
    "cash.payment_refresh": {"class": NEVER, "money": True,
                             "writes": "applies a custodian's transfer status (acknowledge / settle / reject / return) "
                                       "to a payment instruction — a person only"},
    # CASH06 — a cash rule's firing (stage A). DRAFT + SUBMIT only: each instruction lands pending_approval with its
    # approval task, the rule OWNER as its maker, so a DIFFERENT person approves (the cash07 floor). Approve / send /
    # reconcile stay never (above, unregistered). Capped: the rule's own per-firing $ and per-day count (≤ the org
    # ceiling — services.cash_rules) inside the run; here the quantity is the most instructions one firing can draft
    # (its destinations, ≤ 5) against the org's cap_per_run. Engine-only: never offered to the assistant.
    # CPSIM03 — the custodian-API route's two PERSON actions (services.custodian_execution). Declared NEVER (trades) and
    # deliberately NOT registered as actions: no workflow, no agent and no trigger can send an order or import fills.
    "execution.custodian_send": {"class": NEVER, "trades": True,
                                 "writes": "sends an APPROVED execution instruction to a (simulated) custodian's orders "
                                           "API — a person only"},
    "execution.custodian_import": {"class": NEVER, "trades": True,
                                   "writes": "books a custodian's executions as fills (execution_fills) — a person "
                                             "only"},
    # CPSIM05 — the simulated FIX counterparty's AUTOMATIC send / book / reconcile (services.fix_sim_auto). Declared
    # NEVER for unattended runs and NOT registered: it is reachable only from the proposal-execution choke point, the
    # rep2 approval of a proposal-sourced fix_sim instruction and the simulator's clock advance — never a workflow
    # step, an agent or a trigger. Its approval is the proposal's (or rep2's) maker-checker, by a person.
    "execution.fix_sim_auto": {"class": NEVER, "trades": True,
                               "writes": "sends an APPROVED proposal's trades to the SIMULATED FIX counterparty and books "
                                         "the fills (simulated counterparty only)"},
    "cash.rule_fire": {"class": ELIGIBLE_WITH_CAP, "draft_only": True, "submits_for_approval": True,
                       "quantity": lambda _ctx: 5,
                       "writes": "payment instruction DRAFTS submitted for approval (payment_instructions, status "
                                 "pending_approval + the approval task) and the rule's firing record — never "
                                 "approves, sends or reconciles"},
}
#: CASH04 — the verbs whose write may only ever DRAFT (the run's output is a draft a person adopts)
DRAFT_ONLY_VERBS = frozenset(k for k, v in VERB_AUTONOMY.items() if v.get("draft_only"))

CLASS_REASONS = {"money": "moves money", "custody": "changes ownership / custody", "authority": "changes authority",
                 "roles": "changes roles or permissions", "trades": "submits a trade",
                 "contacts": "contacts a member or an outside party"}


def verb_autonomy(action) -> dict:
    """The verb's autonomy record. A read is eligible (no side effect); an UNLISTED write is ``never`` (fail closed)."""
    if action is None:
        return {"class": NEVER, "writes": "unknown verb"}
    meta = dict(VERB_AUTONOMY.get(getattr(action, "key", "") or "", {}))
    declared = getattr(action, "autonomy_class", None)
    if declared in CLASSES:
        meta["class"] = declared
    if "class" not in meta:
        meta["class"] = ELIGIBLE if getattr(action, "access_type", None) == "read" else NEVER
        meta.setdefault("writes", "nothing (read)" if meta["class"] == ELIGIBLE else "undeclared — refused")
    return meta


def autonomy_class(action) -> str:
    return verb_autonomy(action)["class"]


def unclassed_verbs(registry) -> list[str]:
    return sorted(a.key for a in registry.all() if getattr(a, "autonomy_class", None) not in CLASSES)


# ── time (injected by the verify; real time otherwise) ──────────────────────────────────────────────────────────────
_CLOCK: Callable[[], datetime] | None = None


def set_clock(fn: Callable[[], datetime] | None) -> None:
    global _CLOCK
    _CLOCK = fn


def now() -> datetime:
    return _CLOCK() if _CLOCK is not None else datetime.now(timezone.utc)


def day_start(ts: datetime) -> datetime:
    ts = ts.astimezone(timezone.utc)
    return datetime(ts.year, ts.month, ts.day, tzinfo=timezone.utc)


# ── settings ────────────────────────────────────────────────────────────────────────────────────────────────────────
KEY_AUTONOMY = "autonomy"
KEY_KILL = "kill_switch"
KEY_LIMITS = "limits"
KEY_SHADOW_RUNS = "shadow_runs_required"
KEY_VERSION = "version_autonomy"
ORG_SWITCH_NAME = "workflows.autonomy"

DEFAULT_LIMITS = {"runs_per_day": 20, "writes_per_run": 5, "writes_per_day": 50, "cap_per_run": 100,
                  "event_runs_per_hour": 20}
LIMIT_BOUNDS = {"runs_per_day": (0, 1000), "writes_per_run": (0, 100), "writes_per_day": (0, 10000),
                "cap_per_run": (0, 100000), "event_runs_per_hour": (0, 1000)}
DEFAULT_SHADOW_RUNS = 3
VERSION_STATES = ("off", "shadow", "on")


class AutonomyError(Exception):
    """A bad request (422)."""


class AutonomyRefused(Exception):
    """A refusal by rule (403)."""

    def __init__(self, message: str, decision: sod.Decision | None = None):
        super().__init__(message)
        self.decision = decision


class AutonomyConflict(Exception):
    """A state conflict (409)."""


class AutonomyHold(Exception):
    """Raised inside a run: the run HOLDS (a limit, a failed / already-attempted unattended write)."""


def _s(v):
    return None if v is None else str(v)


def _j(v, default=None):
    if v is None:
        return default
    if isinstance(v, (str, bytes)):
        try:
            return json.loads(v)
        except (TypeError, ValueError):
            return default
    return v


RET_COLS = ("id::text AS id, owner_scope, org_id::text AS org_id, workflow_version_id::text AS workflow_version_id, "
            "setting_key, setting_value, status, version_no, maker_id::text AS maker_id, checker_id::text AS checker_id, "
            "requires_checker, reason, decided_at, decision_note, valid_from, valid_to, created_at")
SETTINGS_SQL = f"SELECT {RET_COLS} FROM public.workflow_autonomy_settings"


async def effective_settings(conn, org_id, workflow_version_id=None) -> dict:
    """What is in force NOW for an org (and a version): the org switch, both kill switches, the limits, the shadow
    requirement and the version's state. No row → the safe default (autonomy OFF, version off)."""
    rows = await conn.fetch(
        SETTINGS_SQL + " WHERE status = 'active' AND valid_to IS NULL AND (org_id IS NULL OR (org_id = $1::uuid "
        "AND (workflow_version_id IS NULL OR workflow_version_id = $2::uuid)))",
        str(org_id), _s(workflow_version_id))
    out = {"autonomy": False, "org_kill_switch": False, "platform_kill_switch": False,
           "limits": dict(DEFAULT_LIMITS), "shadow_runs_required": DEFAULT_SHADOW_RUNS, "version_state": "off",
           "rows": {}}
    for r in rows:
        v = _j(r["setting_value"])
        k = r["setting_key"]
        if r["owner_scope"] == "platform" and k == KEY_KILL:
            out["platform_kill_switch"] = bool(v)
        elif k == KEY_AUTONOMY:
            out["autonomy"] = v is True
        elif k == KEY_KILL:
            out["org_kill_switch"] = bool(v)
        elif k == KEY_LIMITS and isinstance(v, dict):
            out["limits"].update({x: int(v[x]) for x in DEFAULT_LIMITS if isinstance(v.get(x), int)})
        elif k == KEY_SHADOW_RUNS and isinstance(v, int):
            out["shadow_runs_required"] = v
        elif k == KEY_VERSION and isinstance(v, dict):
            out["version_state"] = v.get("state") if v.get("state") in VERSION_STATES else "off"
        out["rows"][f"{r['owner_scope']}:{k}"] = r["id"]
    return out


def _validate_value(key: str, value):
    if key in (KEY_AUTONOMY, KEY_KILL):
        if not isinstance(value, bool):
            raise AutonomyError(f"{key} is true or false")
        return value
    if key == KEY_LIMITS:
        if not isinstance(value, dict) or set(value) - set(DEFAULT_LIMITS):
            raise AutonomyError(f"limits names only {sorted(DEFAULT_LIMITS)}")
        out = {}
        for k, v in value.items():
            lo, hi = LIMIT_BOUNDS[k]
            if not isinstance(v, int) or isinstance(v, bool) or not lo <= v <= hi:
                raise AutonomyError(f"limits.{k} is a whole number between {lo} and {hi}")
            out[k] = v
        return out
    if key == KEY_SHADOW_RUNS:
        if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 50:
            raise AutonomyError("shadow_runs_required is a whole number between 1 and 50")
        return value
    if key == KEY_VERSION:
        state = value.get("state") if isinstance(value, dict) else value
        if state not in VERSION_STATES:
            raise AutonomyError(f"a version's state is one of {VERSION_STATES}")
        return {"state": state}
    raise AutonomyError(f"unknown setting {key!r}")


def loosens(key: str, current: dict, value) -> bool:
    """Does the change WIDEN what runs unattended? Loosening needs a different person; tightening applies at once.
    A kill switch is the exception both ways: stopping is immediate, and clearing it is immediate too (clearing it
    turns nothing on by itself — the org switch and each version's opt-in were already checked by a second person)."""
    if key == KEY_AUTONOMY:
        return value is True and not current["autonomy"]
    if key == KEY_KILL:
        return False
    if key == KEY_LIMITS:
        return any(value.get(k, current["limits"][k]) > current["limits"][k] for k in DEFAULT_LIMITS)
    if key == KEY_SHADOW_RUNS:
        return value < current["shadow_runs_required"]
    if key == KEY_VERSION:
        return value["state"] == "on" and current["version_state"] != "on"
    return True


async def _audit(conn, org_id, user_id, action: str, resource_type: str, resource_id, payload: dict) -> None:
    await conn.execute(
        "INSERT INTO audit_log (org_id, user_id, action, resource_type, resource_id, payload) "
        "VALUES ($1::uuid, $2::uuid, $3, $4, $5::uuid, $6::jsonb)",
        str(org_id), _s(user_id), action, resource_type, _s(resource_id), json.dumps(payload, default=str))


def _row_out(r) -> dict:
    d = dict(r)
    d["setting_value"] = _j(d.get("setting_value"))
    for k in ("decided_at", "valid_from", "valid_to", "created_at"):
        if d.get(k) is not None and hasattr(d[k], "isoformat"):
            d[k] = d[k].isoformat()
    return d


async def version_org(conn, workflow_version_id) -> dict | None:
    r = await conn.fetchrow(
        "SELECT v.id::text AS id, v.org_id::text AS org_id, v.version_number, v.workflow_definition_id::text AS "
        "definition_id, d.name FROM workflow_versions v JOIN workflow_definitions d ON d.id = v.workflow_definition_id "
        "WHERE v.id = $1::uuid", str(workflow_version_id))
    return dict(r) if r else None


async def trigger_owners(conn, workflow_version_id) -> list[str]:
    rows = await conn.fetch(
        "SELECT DISTINCT t.created_by::text AS u FROM workflow_triggers t JOIN workflow_versions v "
        "ON v.workflow_definition_id = t.workflow_definition_id WHERE v.id = $1::uuid AND t.created_by IS NOT NULL",
        str(workflow_version_id))
    return [r["u"] for r in rows]


async def shadow_record(conn, org_id, workflow_version_id) -> dict:
    """The version's reviewed shadow runs: clean count, whether the latest review was clean, unreviewed count."""
    r = await conn.fetchrow(
        """SELECT count(*) FILTER (WHERE shadow_review_outcome = 'clean') AS clean,
                  count(*) FILTER (WHERE shadow_review_outcome = 'not_clean') AS not_clean,
                  count(*) FILTER (WHERE shadow_review_outcome IS NULL AND status = 'completed') AS unreviewed,
                  (SELECT shadow_review_outcome FROM workflow_runs x WHERE x.workflow_version_id = $1::uuid
                      AND x.org_id = $2::uuid AND x.autonomy_mode = 'shadow' AND x.shadow_review_outcome IS NOT NULL
                    ORDER BY x.shadow_reviewed_at DESC LIMIT 1) AS latest
             FROM workflow_runs WHERE workflow_version_id = $1::uuid AND org_id = $2::uuid
              AND autonomy_mode = 'shadow'""",
        str(workflow_version_id), str(org_id))
    return {"clean": int(r["clean"]), "not_clean": int(r["not_clean"]), "unreviewed": int(r["unreviewed"]),
            "latest": r["latest"]}


async def _shadow_ready(conn, org_id, workflow_version_id, required: int) -> tuple[bool, str]:
    rec = await shadow_record(conn, org_id, workflow_version_id)
    if rec["clean"] < required:
        return False, (f"this version has {rec['clean']} clean shadow run(s); {required} are required before it may "
                       "run unattended")
    if rec["latest"] != "clean":
        return False, "the most recent shadow review was not clean"
    return True, f"{rec['clean']} clean shadow runs"


async def change_setting(conn, *, org_id, actor_id, is_super_admin: bool, key: str, value, reason: str,
                         platform: bool = False, workflow_version_id=None) -> dict:
    """Ask for a setting change. Tightening is ACTIVE at once; loosening is a DRAFT a different person decides.
    The caller's route gate decides WHO may ask (org settings: manage_org_settings; a version: author_workflows;
    the platform kill switch: Super Admin)."""
    if not actor_id:
        raise AutonomyRefused("a setting change needs a named person")
    reason = (reason or "").strip()
    if not reason:
        raise AutonomyError("say why (reason)")
    if platform:
        if not is_super_admin:
            raise AutonomyRefused("only a Super Admin sets the platform kill switch")
        if key != KEY_KILL:
            raise AutonomyError("the platform carries only its kill switch")
    if (key == KEY_VERSION) != (workflow_version_id is not None):
        raise AutonomyError("a version's opt-in names the version; no other setting does")
    value = _validate_value(key, value)
    target_org = None if platform else str(org_id)
    if workflow_version_id is not None:
        v = await version_org(conn, workflow_version_id)
        if v is None or v["org_id"] != str(org_id):
            raise AutonomyConflict("workflow version not found")
    current = await effective_settings(conn, org_id, workflow_version_id)
    if key == KEY_LIMITS:
        value = {**current["limits"], **value}
    needs_checker = (not platform) and loosens(key, current, value)
    if key == KEY_VERSION and value["state"] == "on":
        ok, why = await _shadow_ready(conn, org_id, workflow_version_id, current["shadow_runs_required"])
        if not ok:
            raise AutonomyRefused(f"opt-in refused: {why}")
    vid = _s(workflow_version_id)
    scope = "platform" if platform else "org"
    async with conn.transaction():
        draft = await conn.fetchval(
            "SELECT id FROM public.workflow_autonomy_settings WHERE status = 'draft' AND setting_key = $1 AND "
            "org_id IS NOT DISTINCT FROM $2::uuid AND workflow_version_id IS NOT DISTINCT FROM $3::uuid",
            key, target_org, vid)
        if draft and needs_checker:
            raise AutonomyConflict("a request for this setting is already awaiting a decision")
        cur = await conn.fetchrow(
            "SELECT id, version_no FROM public.workflow_autonomy_settings WHERE status = 'active' AND valid_to IS NULL "
            "AND setting_key = $1 AND org_id IS NOT DISTINCT FROM $2::uuid AND workflow_version_id IS NOT DISTINCT "
            "FROM $3::uuid FOR UPDATE", key, target_org, vid)
        version_no = (cur["version_no"] + 1) if cur else 1
        ts = now()
        if needs_checker:
            row = await conn.fetchrow(
                "INSERT INTO public.workflow_autonomy_settings (owner_scope, org_id, workflow_version_id, setting_key, "
                "setting_value, requires_checker, reason, status, version_no, maker_id, valid_from) VALUES ($1, "
                f"$2::uuid, $3::uuid, $4, $5::jsonb, true, $6, 'draft', $7, $8::uuid, $9) RETURNING {RET_COLS}",
                scope, target_org, vid, key, json.dumps(value), reason, version_no, str(actor_id), ts)
        else:
            if draft:  # tightening supersedes a pending loosening request for the same setting
                await conn.execute("DELETE FROM public.workflow_autonomy_settings WHERE id = $1", draft)
            if cur is not None:
                await conn.execute("UPDATE public.workflow_autonomy_settings SET status = 'retired', valid_to = $2 "
                                   "WHERE id = $1", cur["id"], ts)
            row = await conn.fetchrow(
                "INSERT INTO public.workflow_autonomy_settings (owner_scope, org_id, workflow_version_id, setting_key, "
                "setting_value, requires_checker, reason, status, version_no, maker_id, decided_at, valid_from) VALUES "
                f"($1, $2::uuid, $3::uuid, $4, $5::jsonb, false, $6, 'active', $7, $8::uuid, $9, $9) RETURNING {RET_COLS}",
                scope, target_org, vid, key, json.dumps(value), reason, version_no, str(actor_id), ts)
        await _audit(conn, org_id, actor_id, "workflow_autonomy.setting_" + ("requested" if needs_checker else "set"),
                     "workflow_autonomy_settings", row["id"],
                     {"scope": scope, "key": key, "value": value, "version_no": version_no,
                      "workflow_version_id": vid, "needs_checker": needs_checker, "reason": reason})
    out = _row_out(row)
    out["needs_checker"] = needs_checker
    return out


async def decide_setting(conn, *, org_id, actor_id, setting_id, outcome: str, note: str | None = None) -> dict:
    """A DIFFERENT person decides a draft — services.sod, maker = the requester (and, for a version opt-in, every
    owner of a trigger that starts that workflow: the trigger owner can never be the checker)."""
    if outcome not in ("approve", "reject"):
        raise AutonomyError("outcome is approve or reject")
    if outcome == "reject" and not (note or "").strip():
        raise AutonomyError("a rejection needs a note saying why")
    async with conn.transaction():
        r = await conn.fetchrow(SETTINGS_SQL + " WHERE id = $1::uuid FOR UPDATE", str(setting_id))
        if r is None or r["org_id"] != str(org_id):
            raise AutonomyConflict("setting request not found")
        if r["status"] != "draft":
            raise AutonomyConflict(f"this request is {r['status']}, not awaiting a decision")
        is_optin = r["setting_key"] == KEY_VERSION
        makers = [r["maker_id"]]
        if is_optin:
            makers += await trigger_owners(conn, r["workflow_version_id"])
        item_type = sod.WORKFLOW_AUTONOMY_OPTIN if is_optin else sod.WORKFLOW_AUTONOMY_SETTING
        await sod.enforce(conn, org_id=org_id, item_type=item_type, checker_id=actor_id, maker_ids=makers,
                          item_id=r["id"], site="workflow_autonomy.decide_setting",
                          refuse=lambda d: AutonomyRefused(
                              "maker-checker: you asked for this change"
                              + (" or own a trigger that starts this workflow" if is_optin else "")
                              + f", so a different person must decide it ({d.reason})", d))
        ts = now()
        if outcome == "reject":
            new = await conn.fetchrow(
                "UPDATE public.workflow_autonomy_settings SET status = 'rejected', checker_id = $2::uuid, "
                "decided_at = $3, decision_note = $4 WHERE id = $1::uuid RETURNING id::text AS id, status",
                r["id"], str(actor_id), ts, note)
        else:
            if is_optin:
                cur_settings = await effective_settings(conn, org_id, r["workflow_version_id"])
                ok, why = await _shadow_ready(conn, org_id, r["workflow_version_id"],
                                              cur_settings["shadow_runs_required"])
                if not ok:
                    raise AutonomyRefused(f"opt-in refused: {why}")
            cur = await conn.fetchrow(
                "SELECT id FROM public.workflow_autonomy_settings WHERE status = 'active' AND valid_to IS NULL "
                "AND setting_key = $1 AND org_id IS NOT DISTINCT FROM $2::uuid AND workflow_version_id IS NOT "
                "DISTINCT FROM $3::uuid FOR UPDATE", r["setting_key"], r["org_id"], r["workflow_version_id"])
            if cur is not None:
                await conn.execute("UPDATE public.workflow_autonomy_settings SET status = 'retired', valid_to = $2 "
                                   "WHERE id = $1", cur["id"], ts)
            new = await conn.fetchrow(
                "UPDATE public.workflow_autonomy_settings SET status = 'active', checker_id = $2::uuid, "
                "decided_at = $3, decision_note = $4 WHERE id = $1::uuid RETURNING id::text AS id, status",
                r["id"], str(actor_id), ts, note)
        await _audit(conn, org_id, actor_id, f"workflow_autonomy.setting_{'approved' if outcome == 'approve' else 'rejected'}",
                     "workflow_autonomy_settings", r["id"],
                     {"key": r["setting_key"], "value": _j(r["setting_value"]), "maker_id": r["maker_id"],
                      "workflow_version_id": r["workflow_version_id"], "note": note})
    return dict(new)


async def review_shadow_run(conn, *, org_id, actor_id, run_id, outcome: str, note: str | None = None) -> dict:
    """The trigger OWNER (any login of the person) reviews a completed shadow run: clean or not clean."""
    if outcome not in ("clean", "not_clean"):
        raise AutonomyError("outcome is clean or not_clean")
    if outcome == "not_clean" and not (note or "").strip():
        raise AutonomyError("say what was wrong (note)")
    r = await conn.fetchrow("SELECT id, org_id::text AS org_id, status, autonomy_mode, shadow_review_outcome, context "
                            "FROM workflow_runs WHERE id = $1::uuid", str(run_id))
    if r is None or r["org_id"] != str(org_id):
        raise AutonomyConflict("run not found")
    if r["autonomy_mode"] != "shadow":
        raise AutonomyConflict("this run is not a shadow run")
    if r["status"] != "completed":
        raise AutonomyConflict(f"a shadow run is reviewed once it has completed (it is {r['status']})")
    if r["shadow_review_outcome"] is not None:
        raise AutonomyConflict("this shadow run has already been reviewed")
    from services.workflow_engine import run_trigger_owner

    owner = await run_trigger_owner(conn, r["context"])
    if owner is None or not await sod.same_person(conn, org_id, actor_id, owner):
        raise AutonomyRefused("the owner of the trigger that started this run reviews its shadow results")
    ts = now()
    async with conn.transaction():
        await conn.execute("UPDATE workflow_runs SET shadow_review_outcome = $2, shadow_reviewed_by = $3::uuid, "
                           "shadow_reviewed_at = $4, shadow_review_note = $5 WHERE id = $1::uuid",
                           str(run_id), outcome, str(actor_id), ts, note)
        await _audit(conn, org_id, actor_id, "workflow_autonomy.shadow_reviewed", "workflow_run", run_id,
                     {"outcome": outcome, "note": note})
    return {"run_id": str(run_id), "outcome": outcome}


# ── the engine's per-step decision ──────────────────────────────────────────────────────────────────────────────────
MODE_ATTENDED = "attended"
MODE_EXECUTE = "execute"
MODE_PLATFORM_JOB = "platform_job"
MODE_SHADOW = "shadow"
MODE_SUSPEND = "suspend"
MODE_HOLD = "hold"


@dataclass
class StepDecision:
    mode: str
    reason: str = ""
    owner: str | None = None
    workflow_name: str | None = None
    version_number: int | None = None
    klass: str | None = None
    settings: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"mode": self.mode, "reason": self.reason, "owner": self.owner, "workflow": self.workflow_name,
                "version": self.version_number, "class": self.klass}


def is_trigger_started(context) -> bool:
    ctx = _j(context, {}) if not isinstance(context, dict) else context
    if not isinstance(ctx, dict):
        return False
    return bool(ctx.get("trigger_id")) and ctx.get("trigger_type") != "manual"


def _is_platform_job_org(org_id) -> bool:
    """The orgs platform jobs may run in — the ONE existing list, security_master_jobs.PLATFORM_JOB_ORG_IDS (the
    Hollisworks platform org; a verify adds its own fixture platform org there for its run)."""
    from services.security_master_jobs import PLATFORM_JOB_ORG_IDS

    try:
        return uuid.UUID(str(org_id)) in PLATFORM_JOB_ORG_IDS
    except (TypeError, ValueError):
        return False


async def _is_super_admin(conn, user_id) -> bool:
    from services.database import platform_scope
    from services.rbac import is_super_admin, load_principal

    async with platform_scope(conn):
        p = await load_principal(conn, user_id)
    return p is not None and is_super_admin(p)


async def _usage(conn, org_id, run_id) -> dict:
    t0 = day_start(now())
    r = await conn.fetchrow(
        """SELECT count(*) FILTER (WHERE workflow_run_id = $2::uuid) AS run_writes,
                  count(*) AS day_writes,
                  count(DISTINCT workflow_run_id) FILTER (WHERE workflow_run_id <> $2::uuid) AS other_runs
             FROM workflow_run_steps
            WHERE org_id = $1::uuid AND autonomy_mode = 'unattended'
              AND (autonomy_claimed_at >= $3 OR workflow_run_id = $2::uuid)""",
        str(org_id), str(run_id), t0)
    return {"run_writes": int(r["run_writes"]), "day_writes": int(r["day_writes"]), "other_runs": int(r["other_runs"])}


def _limit_breach(limits: dict, usage: dict, klass: str, quantity: int | None) -> str | None:
    if usage["run_writes"] == 0 and usage["other_runs"] >= limits["runs_per_day"]:
        return f"limit: runs per day ({limits['runs_per_day']}) reached for unattended runs today"
    if usage["run_writes"] >= limits["writes_per_run"]:
        return f"limit: writes per run ({limits['writes_per_run']}) reached in this run"
    if usage["day_writes"] >= limits["writes_per_day"]:
        return f"limit: writes per day ({limits['writes_per_day']}) reached in this organization today"
    if klass == ELIGIBLE_WITH_CAP:
        if quantity is None:
            return "limit: this verb writes a quantity and none could be read from the run — not run unattended"
        if quantity > limits["cap_per_run"]:
            return f"limit: {quantity} items exceeds the per-run cap ({limits['cap_per_run']})"
    return None


async def decide_step(pool, *, org_id, run_id, run_context, started_by, action, invocable: bool) -> StepDecision:
    """The ONE decision for a non-Tier-1 Service Task (the engine has already suspended Tier 1). Reads committed
    state fresh on every step, so a switch turned off mid-run stops the NEXT step."""
    if not is_trigger_started(run_context) or not invocable or getattr(action, "access_type", None) != "write":
        return StepDecision(MODE_ATTENDED)
    meta = verb_autonomy(action)
    klass = meta["class"]
    async with pool.acquire() as conn:
        from services.workflow_engine import run_trigger_owner

        owner = await run_trigger_owner(conn, run_context)
        info = await conn.fetchrow(
            "SELECT r.workflow_version_id::text AS vid, v.version_number, d.name FROM workflow_runs r "
            "JOIN workflow_versions v ON v.id = r.workflow_version_id JOIN workflow_definitions d "
            "ON d.id = v.workflow_definition_id WHERE r.id = $1::uuid", str(run_id))
        vid = info["vid"] if info else None
        st = await effective_settings(conn, org_id, vid)
        base = dict(owner=_s(owner), workflow_name=info["name"] if info else None,
                    version_number=info["version_number"] if info else None, klass=klass, settings=st)
        if st["platform_kill_switch"]:
            return StepDecision(MODE_SUSPEND, "the platform kill switch is on — unattended execution is stopped", **base)
        if st["org_kill_switch"]:
            return StepDecision(MODE_SUSPEND, "this organization's kill switch is on — unattended execution is stopped",
                                **base)
        if (meta.get("platform_job") and _is_platform_job_org(org_id) and owner is not None
                and await _is_super_admin(conn, owner)):
            return StepDecision(MODE_PLATFORM_JOB, "platform job (its own Super Admin switch)", **base)
        if klass == NEVER:
            why = ", ".join(CLASS_REASONS[k] for k in CLASS_REASONS if meta.get(k)) or "not cleared for autonomy"
            return StepDecision(MODE_SUSPEND, f"{action.key} is never run unattended ({why}) — a person decides", **base)
        if owner is None:
            return StepDecision(MODE_SUSPEND, "the trigger that started this run has no named owner", **base)
        if st["version_state"] == "shadow":
            return StepDecision(MODE_SHADOW, "shadow period: recorded, not executed", **base)
        if st["version_state"] != "on":
            return StepDecision(MODE_SUSPEND, "this workflow version is not opted in to run unattended", **base)
        if not st["autonomy"]:
            return StepDecision(MODE_SUSPEND, f"the organization switch {ORG_SWITCH_NAME} is off", **base)
        quantity = None
        qfn = meta.get("quantity")
        if callable(qfn):
            try:
                quantity = int(qfn(run_context or {}))
            except Exception:  # noqa: BLE001 — an unreadable quantity is no quantity (fail closed below)
                quantity = None
        breach = _limit_breach(st["limits"], await _usage(conn, org_id, run_id), klass, quantity)
        if breach:
            return StepDecision(MODE_HOLD, breach, **base)
        return StepDecision(MODE_EXECUTE, "unattended: both switches on, opted in after the shadow period", **base)


def idempotency_key(run_step_id) -> str:
    return f"wfstep:{run_step_id}"


async def claim_step(pool, *, org_id, run_id, run_step_id, decision: StepDecision, action_key: str) -> tuple[str, Any]:
    """Claim the run-step ONCE, committed before the verb runs, with the delegate audit line. Returns
    ('claimed', None), ('done', prior_result) when it already completed, or ('attempted', None) when an earlier
    attempt started and did not finish — that one is never retried (no double write, no retry storm)."""
    from services.workflow_engine import _apply_rls, _independent_acquire

    key = idempotency_key(run_step_id)
    async with _independent_acquire(pool) as c:
        async with c.transaction():
            await _apply_rls(c)
            row = await c.fetchrow(
                "UPDATE workflow_run_steps SET idempotency_key = $2, autonomy_mode = 'unattended', "
                "delegate_for = $3::uuid, autonomy_claimed_at = $4, status = 'executing', started_at = $4 "
                "WHERE id = $1::uuid AND idempotency_key IS NULL RETURNING id",
                str(run_step_id), key, decision.owner, now())
            if row is None:
                prior = await c.fetchrow("SELECT status, result FROM workflow_run_steps WHERE id = $1::uuid",
                                         str(run_step_id))
                if prior is not None and prior["status"] == "completed":
                    return "done", _j(prior["result"], {})
                return "attempted", None
            await c.execute("UPDATE workflow_runs SET autonomy_mode = 'unattended' WHERE id = $1::uuid "
                            "AND autonomy_mode IS DISTINCT FROM 'unattended'", str(run_id))
            await _audit(c, org_id, decision.owner, "workflow.unattended_write", "workflow_run_step", run_step_id,
                         {"line": delegate_line(decision), "verb": action_key, "workflow_run_id": str(run_id),
                          "idempotency_key": key, "on_behalf_of": decision.owner,
                          "workflow": decision.workflow_name, "version": decision.version_number})
    return "claimed", None


def delegate_line(decision: StepDecision) -> str:
    return (f"on behalf of {decision.owner} by workflow {decision.workflow_name} v{decision.version_number}")


async def finish_step(pool, *, run_step_id, result: dict | None, error: str | None = None) -> None:
    """Record the claimed step's outcome, committed at once (a later hold of the run must not erase it)."""
    from services.workflow_engine import _apply_rls, _independent_acquire

    async with _independent_acquire(pool) as c:
        async with c.transaction():
            await _apply_rls(c)
            await c.execute(
                "UPDATE workflow_run_steps SET status = $2, result = $3::jsonb, error_detail = $4, completed_at = $5 "
                "WHERE id = $1::uuid", str(run_step_id), "failed" if error else "completed",
                json.dumps(result or {}, default=str), error, now())


def shadow_result(action, decision: StepDecision, *, org_id, run_id, run_context) -> dict:
    """What the write WOULD have been — nothing is executed, no handler is called."""
    meta = verb_autonomy(action)
    preview = meta.get("shadow_preview")
    would = None
    if callable(preview):
        try:
            would = preview(run_context or {})
        except Exception as exc:  # noqa: BLE001
            would = {"preview_error": f"{type(exc).__name__}: {exc}"}
    return {
        "action_registry_key": action.key, "resolved": True, "access_type": action.access_type, "invoked": False,
        "executed_at": None,
        "autonomy": dict(decision.as_dict(), mode=MODE_SHADOW),
        "shadow": {"verb": action.key,
                   "arguments": {"org_id": str(org_id), "user_id": decision.owner, "workflow_run_id": str(run_id),
                                 "run_context": run_context or {}},
                   "target": meta.get("writes"),
                   "would_produce": would if would is not None else
                   f"no preview: {action.key} would call {getattr(action.handler, '__qualname__', 'its handler')} "
                   "with these arguments",
                   "executed": False, "recorded_at": now().isoformat()},
    }


async def hold_alerts(pool, *, org_id, run_id, owner, reason: str) -> list:
    """Over a limit / a failure: alert the trigger owner AND every org admin (member_todos, not a new channel)."""
    from services import workflow_todos
    from services.workflow_engine import _apply_rls, _independent_acquire

    recipients = set(await workflow_todos._org_admin_recipients(org_id))
    if owner:
        recipients.add(str(owner))
    ids = []
    async with _independent_acquire(pool) as c:
        async with c.transaction():
            await _apply_rls(c)
            for uid in sorted(recipients):
                ids.append(await workflow_todos._upsert_todo(
                    c, org_id=org_id, user_id=uid, source=TODO_SOURCE_HOLD, related_type="workflow_run",
                    related_id=run_id, title="Unattended workflow held", detail=reason[:2000], priority=5,
                    action_key="/admin/workflows/runs"))
    return ids


#: Event types a trigger may listen for (Task 6). document_confirmed is published by services.chancery_workflow_bridge;
#: the rest by services.domain_events.publish_event (a document filed, a task completed, a held-away import approved).
TRIGGER_EVENT_TYPES = ("document_confirmed", "human_task.opened", "human_task.completed",
                       "held_away.import_loaded", "held_away.import_approved",
                       "cash.incoming_credit")   # CASH06 — an incoming credit on the feed (services.cash_rules)

TODO_SOURCE_HOLD = "workflow_autonomy_hold"
TODO_SOURCE_DIGEST = "workflow_autonomy_digest"


# ── Task 6: event rate limit ────────────────────────────────────────────────────────────────────────────────────────
async def event_rate_limited(conn, *, org_id, workflow_version_id) -> tuple[bool, int, int]:
    """Runs a workflow VERSION started from events in the last hour (any of its triggers) against the org's limit —
    per version, so a second trigger on the same workflow cannot carry an event storm past it."""
    st = await effective_settings(conn, org_id)
    limit = st["limits"]["event_runs_per_hour"]
    n = await conn.fetchval(
        "SELECT count(*) FROM domain_event_deliveries WHERE workflow_version_id = $1::uuid "
        "AND status IN ('STARTING', 'DELIVERED') AND created_at > $2", str(workflow_version_id),
        now() - timedelta(hours=1))
    return int(n) >= limit, int(n), limit


# ── Task 2: validation of a trigger-started workflow ────────────────────────────────────────────────────────────────
async def never_steps(conn, *, definition_id, org_id) -> list[dict]:
    """Service steps of the definition's CURRENT version that a trigger would start and that may never run unattended:
    a ``never`` write verb whose effective tier is not 1 (a Tier-1 step suspends for a person, so it is allowed)."""
    from services.action_registry import REGISTRY
    from services.workflow_engine import SUSPEND_TIER, compute_effective_tier

    rows = await conn.fetch(
        "SELECT ws.step_key, ws.display_name, ws.action_registry_key, ws.autonomy_tier FROM workflow_steps ws "
        "JOIN workflow_versions v ON v.id = ws.workflow_version_id WHERE v.workflow_definition_id = $1::uuid "
        "AND v.org_id = $2::uuid AND v.is_current AND ws.step_type = 'service'", str(definition_id), str(org_id))
    out = []
    for r in rows:
        a = REGISTRY.get(r["action_registry_key"]) if r["action_registry_key"] else None
        if a is None or a.access_type != "write" or not a.workflow_invocable:
            continue
        if autonomy_class(a) == NEVER and compute_effective_tier(dict(r), a) != SUSPEND_TIER:
            out.append({"step_key": r["step_key"], "display_name": r["display_name"], "verb": a.key})
    return out


# ── Task 7: visibility ──────────────────────────────────────────────────────────────────────────────────────────────
def eligibility_list(registry) -> list[dict]:
    out = []
    for a in sorted(registry.all(), key=lambda x: x.key):
        m = verb_autonomy(a)
        out.append({"key": a.key, "module": a.module, "access_type": a.access_type, "tier": a.tier,
                    "duty": getattr(a, "duty", None), "required_permission": a.required_permission,
                    "workflow_invocable": a.workflow_invocable, "autonomy_class": m["class"],
                    "platform_job": bool(m.get("platform_job")), "writes": m.get("writes"),
                    "why_never": [CLASS_REASONS[k] for k in CLASS_REASONS if m.get(k)]})
    return out


async def overview(conn, *, org_id) -> dict:
    from services.action_registry import REGISTRY

    st = await effective_settings(conn, org_id)
    rows = await conn.fetch(SETTINGS_SQL + " WHERE (org_id = $1::uuid OR org_id IS NULL) ORDER BY created_at DESC "
                            "LIMIT 200", str(org_id))
    usage = await conn.fetchrow(
        "SELECT count(*) AS writes, count(DISTINCT workflow_run_id) AS runs FROM workflow_run_steps "
        "WHERE org_id = $1::uuid AND autonomy_mode = 'unattended' AND autonomy_claimed_at >= $2",
        str(org_id), day_start(now()))
    pending = await conn.fetch(
        "SELECT r.id::text AS run_id, r.workflow_version_id::text AS workflow_version_id, v.version_number, d.name, "
        "r.completed_at FROM workflow_runs r JOIN workflow_versions v ON v.id = r.workflow_version_id "
        "JOIN workflow_definitions d ON d.id = v.workflow_definition_id WHERE r.org_id = $1::uuid "
        "AND r.autonomy_mode = 'shadow' AND r.status = 'completed' AND r.shadow_review_outcome IS NULL "
        "ORDER BY r.completed_at DESC LIMIT 100", str(org_id))
    versions = await conn.fetch(
        SETTINGS_SQL + " WHERE org_id = $1::uuid AND setting_key = 'version_autonomy' AND status = 'active' "
        "AND valid_to IS NULL", str(org_id))
    return {
        "switches": {"org_switch": ORG_SWITCH_NAME, "autonomy": st["autonomy"],
                     "org_kill_switch": st["org_kill_switch"], "platform_kill_switch": st["platform_kill_switch"]},
        "limits": st["limits"], "shadow_runs_required": st["shadow_runs_required"],
        "usage_today": {"writes": int(usage["writes"]), "runs": int(usage["runs"])},
        "settings": [_row_out(r) for r in rows],
        "drafts": [_row_out(r) for r in rows if r["status"] == "draft"],
        "versions": [_row_out(r) for r in versions],
        "shadow_reviews_pending": [dict(r, completed_at=r["completed_at"].isoformat() if r["completed_at"] else None)
                                   for r in pending],
        "verbs": eligibility_list(REGISTRY),
        "classes": list(CLASSES),
        "version_states": list(VERSION_STATES),
        "limit_keys": list(DEFAULT_LIMITS),
    }


async def daily_digest(conn, *, org_id, day: datetime) -> list:
    """One to-do per org admin summarising the day's unattended writes (refreshed, never duplicated)."""
    from services import workflow_todos

    t0 = day_start(day)
    rows = await conn.fetch(
        "SELECT d.name, v.version_number, count(*) AS n FROM workflow_run_steps rs JOIN workflow_runs r "
        "ON r.id = rs.workflow_run_id JOIN workflow_versions v ON v.id = r.workflow_version_id JOIN "
        "workflow_definitions d ON d.id = v.workflow_definition_id WHERE rs.org_id = $1::uuid "
        "AND rs.autonomy_mode = 'unattended' AND rs.autonomy_claimed_at >= $2 AND rs.autonomy_claimed_at < $3 "
        "GROUP BY 1, 2 ORDER BY 1, 2", str(org_id), t0, t0 + timedelta(days=1))
    if not rows:
        return []
    total = sum(int(r["n"]) for r in rows)
    detail = f"{total} unattended write(s) on {t0.date().isoformat()}: " + "; ".join(
        f"{r['name']} v{r['version_number']} × {r['n']}" for r in rows)
    digest_id = uuid.uuid5(uuid.NAMESPACE_URL, f"s29b-digest:{org_id}:{t0.date().isoformat()}")
    ids = []
    for uid in sorted(await workflow_todos._org_admin_recipients(org_id)):
        ids.append(await workflow_todos._upsert_todo(
            conn, org_id=org_id, user_id=uid, source=TODO_SOURCE_DIGEST, related_type="workflow_autonomy_digest",
            related_id=digest_id, title="Unattended workflow writes — daily digest", detail=detail[:2000],
            priority=50, action_key="/admin/workflows/autonomy"))
    return ids
