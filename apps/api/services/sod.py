"""S30 — SEGREGATION OF DUTIES: the ONE maker-checker rule every approval site calls.

    ctx = await sod.load(conn, org_id=…, item_type="trade_block", ids=[checker, *makers])   # ONE read
    d   = ctx.evaluate(checker_id=…, maker_ids=[…])                                          # pure
    d   = await sod.can_check(conn, …)          # load + evaluate
    d   = await sod.enforce(conn, …, refuse=lambda d: MakerCheckerViolation(msg))
                                                # can_check + record_decision + raise on refusal

THE MODEL (docs/SEGREGATION_OF_DUTIES.md; schema docs/s30_part1.sql):

* ``sod_duties`` — a duty per action class (propose_trade, approve_trade, …) and the permissions that grant it.
* ``sod_item_types`` — every item type an approval site decides: its maker duty, checker duty, FLOOR class
  (trade / transfer / disclosure / role_grant), whether its table can record a disclosed self-approval, and the
  rule that was in force before S30 (``pre_s30_rule``: 'maker_ne_checker' everywhere except role grants: 'none').
* ``sod_rules`` — versioned: platform FLOOR rules (never relaxed, never self-approved), platform DEFAULT rules,
  platform default POLICIES (``self_approval`` + ``enforce_floor`` per item type, seeded EQUAL to the pre-S30
  behaviour) and an org's own stricter rules and policy overrides. Every edit is maker-checker.
* ``sod_decisions`` — every real decision, append-only: the self-approval log and the refused-checks report.

PERSONS, NOT LOGINS. Two logins of one person are one ``users`` row (``user_identities``) — and where a person once
had a SECOND row (a placeholder sign-in later linked: ``user_person_links``, or a duplicate merged by
``services.users``: a retired row whose ``auth0_sub`` is another user's linked identity) that row resolves to the
person too. With the org's ``enforce_floor`` on for the item type, maker and checker are compared as PERSONS; off,
the pre-S30 rule is applied to the raw ids (and a same-person match is reported as a warning, never silently).

DECISION ORDER (``Context.evaluate``):
  1. no catalog row for the item type → REFUSED (fail closed; never "no rule → allow");
  2. the checker IS a maker (person, or raw id when the floor is not enforced):
       floor item type                                  → REFUSED by the floor rule (Super Admin included)
       self-approval capable + policy 'when_no_other_checker' + the eligible set genuinely EMPTY
                                                        → SELF_APPROVAL_ALLOWED with a disclosure
       otherwise                                        → REFUSED by the default rule
     (an item type whose pre-S30 rule is 'none' with the floor not enforced → ALLOWED, with a warning);
  3. the org's own rules (same household / same period, block or warn) → REFUSED or a warning;
  4. ALLOWED.

Deterministic: no clock inside ``evaluate`` (the history window is applied in the one read).
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

logger = logging.getLogger(__name__)

ALLOWED = "allowed"
REFUSED = "refused"
SELF_APPROVAL_ALLOWED = "self_approval_allowed"

OUTCOME_RECORDED = {ALLOWED: "allowed", REFUSED: "refused", SELF_APPROVAL_ALLOWED: "self_approved"}

SELF_APPROVAL_REASON = "no other eligible checker"
TODO_SOURCE_SELF_APPROVAL = "sod_self_approval"

# ── Item types (keys of public.sod_item_types; labels live in the table, Rule 1) ──────────────────────────────────
# One constant per approval site family, so a site names its item type without a string typo. The table is the
# truth; ``verify_s30`` asserts every constant has a catalog row and every catalog row a caller.
AGENT_PROPOSAL = "agent_proposal"
WORKFLOW_STEP = "workflow_step"
WORKFLOW_USER_TASK = "workflow_user_task"
HUMAN_TASK = "human_task"
ACCOUNT_MODEL_LINK = "account_model_link"
AUTHORITY_CHANGE = "authority_change"
LOT_METHOD_CHANGE = "lot_method_change"
ALLOCATION_POLICY = "allocation_policy"
ALLOCATION_CORRECTION = "allocation_correction"
TRADE_BLOCK = "trade_block"
EXECUTION_INSTRUCTION = "execution_instruction"
REBALANCE_PROPOSAL = "rebalance_proposal"
GIFT_PROPOSAL = "gift_proposal"
TILT_SET = "tilt_set"
SECTION351_PLAN = "section351_plan"
PROVIDER_CATALOG_VERSION = "provider_catalog_version"
PROVIDER_SUBSCRIPTION = "provider_subscription"
MODEL_VERSION = "model_version"
NOTE_REPLICA = "note_replica"
OPTION_PACKAGE = "option_package"
MARGIN_AGREEMENT = "margin_agreement"
HELD_AWAY_IMPORT = "held_away_import"
PORTFOLIO_IMPORT = "portfolio_import"
HYPOTHETICAL_POLICY = "hypothetical_policy"
SUBSTITUTION_SET = "substitution_set"
SUBSTITUTION_DENIAL = "substitution_denial"
TAX_PROFILE = "tax_profile"
LOSS_CARRYFORWARD = "loss_carryforward"
REPLICA_TERMS_VERSION = "replica_terms_version"
WORKFLOW_TEMPLATE_VERSION = "workflow_template_version"
DISCLOSURE_VERSION = "disclosure_version"
DATA_UPLOAD = "data_upload"
FEE_RUN = "fee_run"
CARRY_RUN = "carry_run"
MONEY_MOVEMENT = "money_movement"
ROLE_GRANT = "role_grant"
SOD_RULE = "sod_rule"
SOD_PLATFORM_RULE = "sod_platform_rule"
WORKFLOW_AUTONOMY_SETTING = "workflow_autonomy_setting"   # S29B: an org's autonomy switch / limit loosened
WORKFLOW_AUTONOMY_OPTIN = "workflow_autonomy_optin"       # S29B: a workflow version signed off to run unattended
CASH_POLICY = "cash_policy"                               # CASH02: a household's / entity's cash policy (a target)
PAYMENT_INSTRUCTION = "payment_instruction"               # CASH07: money between a family's own accounts — FLOOR
CASH_RULE = "cash_rule"                                   # CASH06: a standing rule that drafts payments — FLOOR
PAYEE = "payee"                                           # CASH08: a household payee — FLOOR
PAYEE_BANK_CHANGE = "payee_bank_change"                   # CASH08: a payee's bank-detail change (call-back) — FLOOR
FIRST_PAYEE_PAYMENT = "first_payment_to_new_payee"        # CASH08: first payment to a payee (dormant) — FLOOR
THIRD_PARTY_SETTING = "third_party_payments_setting"      # CASH08: the third-party switch (two Super Admins) — FLOOR

ITEM_TYPES = (
    AGENT_PROPOSAL, WORKFLOW_STEP, WORKFLOW_USER_TASK, HUMAN_TASK, ACCOUNT_MODEL_LINK, AUTHORITY_CHANGE,
    LOT_METHOD_CHANGE, ALLOCATION_POLICY, ALLOCATION_CORRECTION, TRADE_BLOCK, EXECUTION_INSTRUCTION,
    REBALANCE_PROPOSAL, GIFT_PROPOSAL, TILT_SET, SECTION351_PLAN, PROVIDER_CATALOG_VERSION, PROVIDER_SUBSCRIPTION,
    MODEL_VERSION, NOTE_REPLICA, OPTION_PACKAGE, MARGIN_AGREEMENT, HELD_AWAY_IMPORT, PORTFOLIO_IMPORT,
    HYPOTHETICAL_POLICY, SUBSTITUTION_SET, SUBSTITUTION_DENIAL, TAX_PROFILE, LOSS_CARRYFORWARD,
    REPLICA_TERMS_VERSION, WORKFLOW_TEMPLATE_VERSION, DISCLOSURE_VERSION, DATA_UPLOAD, FEE_RUN, CARRY_RUN,
    MONEY_MOVEMENT, ROLE_GRANT, SOD_RULE, SOD_PLATFORM_RULE, WORKFLOW_AUTONOMY_SETTING, WORKFLOW_AUTONOMY_OPTIN,
    CASH_POLICY, PAYMENT_INSTRUCTION, CASH_RULE, PAYEE, PAYEE_BANK_CHANGE, FIRST_PAYEE_PAYMENT, THIRD_PARTY_SETTING,
)

# services.human_tasks kinds → the item type their task decides (a kind not listed is the generic HUMAN_TASK)
TASK_KIND_ITEM_TYPES: dict[str, str] = {
    "account_model_link_approval": ACCOUNT_MODEL_LINK,
    "approve_authority_change": AUTHORITY_CHANGE,
    "approve_lot_method_change": LOT_METHOD_CHANGE,
    "allocation_policy_approval": ALLOCATION_POLICY,
    "block_approval": TRADE_BLOCK,
    "block_allocation_correction": ALLOCATION_CORRECTION,
    "execution_approval": EXECUTION_INSTRUCTION,
    "gift_proposal_approval": GIFT_PROPOSAL,
    "tilt_set_approval": TILT_SET,
    "section351_plan_approval": SECTION351_PLAN,
    "catalog_version_publish": PROVIDER_CATALOG_VERSION,
    "model_version_approval": MODEL_VERSION,
    "note_replica_approval": NOTE_REPLICA,
    "option_package_approval": OPTION_PACKAGE,
    "rebalance_proposal_approval": REBALANCE_PROPOSAL,
    "margin_agreement_approval": MARGIN_AGREEMENT,
    "held_away_import_approval": HELD_AWAY_IMPORT,
    "integrated_import_approval": PORTFOLIO_IMPORT,
    "hypothetical_policy_approval": HYPOTHETICAL_POLICY,
    "substitution_set_approval": SUBSTITUTION_SET,
    "substitution_denial_approval": SUBSTITUTION_DENIAL,
    "tax_profile_approval": TAX_PROFILE,
    "loss_carryforward_approval": LOSS_CARRYFORWARD,
    "cash_policy_approval": CASH_POLICY,
    "payment_instruction_approval": PAYMENT_INSTRUCTION,   # CASH07 — the floor item type (transfer)
    "cash_rule_approval": CASH_RULE,                       # CASH06 — activate / resume a cash rule (floor, transfer)
    "payee_approval": PAYEE,                               # CASH08 — approve a payee (floor)
    "payee_bank_change_approval": PAYEE_BANK_CHANGE,       # CASH08 — approve a bank-detail change (floor, call-back)
    "third_party_switch_approval": THIRD_PARTY_SETTING,    # CASH08 — the third-party switch (floor, two Super Admins)
}


def item_type_for_task_kind(kind: str | None) -> str:
    return TASK_KIND_ITEM_TYPES.get(kind or "", HUMAN_TASK)


FLOOR_CLASSES = ("trade", "transfer", "disclosure", "role_grant")


class SoDError(Exception):
    """A structural problem calling the service (bad arguments, a missing input the rule needs)."""


class SoDRefused(Exception):
    """The default refusal ``enforce`` raises when a site passes no ``refuse`` factory."""

    def __init__(self, decision: "Decision"):
        super().__init__(decision.reason)
        self.decision = decision


# ── Decision ────────────────────────────────────────────────────────────────────────────────────────────────────────
@dataclass
class Decision:
    outcome: str
    reason: str
    org_id: str
    item_type: str
    checker_id: str | None
    checker_person: str | None
    maker_ids: list[str]
    maker_persons: list[str]
    rule: dict | None = None
    disclosure: str | None = None
    warnings: list[dict] = field(default_factory=list)
    same_person: bool = False
    item_id: str | None = None
    household_id: str | None = None
    site: str | None = None
    decision_id: str | None = None

    @property
    def allowed(self) -> bool:
        """May the checker act — includes a governed self-approval (the caller records the disclosure)."""
        return self.outcome in (ALLOWED, SELF_APPROVAL_ALLOWED)

    @property
    def refused(self) -> bool:
        return self.outcome == REFUSED

    @property
    def self_approval(self) -> bool:
        return self.outcome == SELF_APPROVAL_ALLOWED

    def as_dict(self) -> dict:
        return {"outcome": self.outcome, "reason": self.reason, "item_type": self.item_type,
                "item_id": self.item_id, "checker_person": self.checker_person,
                "maker_persons": self.maker_persons, "same_person": self.same_person,
                "rule": self.rule, "disclosure": self.disclosure, "warnings": self.warnings,
                "decision_id": self.decision_id}


def _s(v) -> str | None:
    if v is None:
        return None
    return str(v)


def _ids(values: Iterable) -> list[str]:
    out: list[str] = []
    for v in values or ():
        if v is None:
            continue
        try:
            s = str(uuid.UUID(str(v)))
        except (TypeError, ValueError):
            raise SoDError(f"not a user id: {v!r}") from None
        if s not in out:
            out.append(s)
    return out


def _loads(v, default):
    if v is None:
        return default
    if isinstance(v, (dict, list)):
        return v
    return json.loads(v)


# ── Context: the ONE read, then pure evaluation ─────────────────────────────────────────────────────────────────────
@dataclass
class Context:
    org_id: str
    item_type: str
    item: dict | None                     # the sod_item_types row (None → fail closed)
    rules: list[dict]                     # active platform + this org's rules for the item type
    persons: dict[str, dict]              # user id → {"person", "linked", "via"}
    duties: dict[str, dict]               # duty_key → row (labels)
    maker_duty_of: dict[str, str]         # item_type → maker duty (for household history)
    household_history: list[dict]         # this household's recorded decisions inside the widest window
    period_history: list[dict]            # reciprocal-approval history inside the widest window
    household_id: str | None = None

    # ── policy ──
    def policy(self) -> dict:
        """The org's policy row for this item type if it has one, else the platform default (else fail-safe)."""
        org = [r for r in self.rules if r["rule_kind"] == "policy" and r["org_id"] is not None]
        plat = [r for r in self.rules if r["rule_kind"] == "policy" and r["org_id"] is None]
        chosen = (org or plat or [None])[0]
        if chosen is None:
            return {"self_approval": "never", "enforce_floor": True, "rule": None, "source": "built-in"}
        # S29B (Task 0): an organization can only TIGHTEN the floor. When the platform policy enforces it (role grants
        # from S29B on), an org policy row that says otherwise is ignored for enforce_floor — never relaxed by an org.
        enforce = bool(chosen["enforce_floor"]) or any(bool(p["enforce_floor"]) for p in plat)
        return {"self_approval": chosen["self_approval"], "enforce_floor": enforce,
                "rule": chosen, "source": "org" if chosen["org_id"] else "platform"}

    def floor_rule(self) -> dict | None:
        return next((r for r in self.rules if r["rule_kind"] == "floor"), None)

    def default_rule(self) -> dict | None:
        return next((r for r in self.rules if r["rule_kind"] == "default"), None)

    def org_rules(self) -> list[dict]:
        return [r for r in self.rules if r["rule_kind"] == "org"]

    def person(self, user_id: str | None, *, enforce: bool = True) -> str | None:
        if user_id is None:
            return None
        if not enforce:
            return user_id
        return (self.persons.get(user_id) or {}).get("person") or user_id

    def label(self, duty_key: str | None) -> str:
        return (self.duties.get(duty_key or "") or {}).get("label") or (duty_key or "?")

    # ── the rule ──
    def evaluate(self, *, checker_id, maker_ids, other_checker_exists: bool | None = None,
                 related_makers: dict[str, list] | None = None, item_id=None, site: str | None = None) -> Decision:
        checker = _ids([checker_id])[0] if checker_id is not None else None
        makers = _ids(maker_ids)
        base = dict(org_id=self.org_id, item_type=self.item_type, checker_id=checker, maker_ids=makers,
                    item_id=_s(item_id), household_id=self.household_id, site=site)
        if self.item is None:
            return Decision(outcome=REFUSED, reason=f"no segregation-of-duties catalog row for item type "
                            f"{self.item_type!r}: refused (fail closed)", checker_person=None, maker_persons=[],
                            **base)
        if checker is None:
            return Decision(outcome=REFUSED, reason="a check needs a person to make it", checker_person=None,
                            maker_persons=[], **base)
        pol = self.policy()
        enforce = pol["enforce_floor"]
        c_person = self.person(checker)
        m_persons = [self.person(m) for m in makers]
        same_person = c_person in m_persons
        raw_same = checker in makers
        same = same_person if enforce else raw_same
        floor = self.floor_rule()
        default = self.default_rule()
        warnings: list[dict] = []
        if not enforce and same_person and not raw_same:
            warnings.append({"code": "same_person_floor_not_enforced",
                             "message": "the checker and the maker are the same person through a linked login; "
                                        "this organization has not turned on the floor for this item type, so the "
                                        "rule in force before S30 (login ids) decided"})
        decided = dict(base, checker_person=c_person if enforce else checker,
                       maker_persons=m_persons if enforce else makers, same_person=same_person)
        item_label = self.item["label"]
        if same:
            if not enforce and self.item.get("pre_s30_rule") == "none":
                warnings.append({"code": "floor_not_enforced",
                                 "message": f"{item_label}: the floor (the same person may not "
                                            f"{self.label(self.item['maker_duty'])} and "
                                            f"{self.label(self.item['checker_duty'])}) is not yet enforced for this "
                                            "organization (sod.enforce_floor is off); allowed as before S30",
                                 "rule": _rule_ref(floor)})
                return Decision(outcome=ALLOWED, reason="allowed: floor not enforced for this item type (as before "
                                "S30)", rule=_rule_ref(pol["rule"]), warnings=warnings, **decided)
            if self.item.get("floor_class"):
                return Decision(outcome=REFUSED, rule=_rule_ref(floor), warnings=warnings,
                                reason=f"floor rule: the same person may not {self.label(self.item['maker_duty'])} "
                                       f"and {self.label(self.item['checker_duty'])} on the same {item_label.lower()}"
                                       " — no organization setting and no role (Super Admin included) relaxes it",
                                **decided)
            if self.item.get("self_approval_capable") and pol["self_approval"] == "when_no_other_checker":
                if other_checker_exists is None:
                    raise SoDError(f"{self.item_type}: self-approval is governed by the eligible-checker set — the "
                                   "caller must pass other_checker_exists (sod.eligible_checkers)")
                if not other_checker_exists:
                    return Decision(outcome=SELF_APPROVAL_ALLOWED, rule=_rule_ref(pol["rule"]), warnings=warnings,
                                    reason=f"self-approval allowed: {SELF_APPROVAL_REASON} in this organization",
                                    disclosure=f"Self-approved — {SELF_APPROVAL_REASON} holds "
                                               f"{self.label(self.item['checker_duty']).lower()} in this "
                                               "organization. Recorded for compliance review.",
                                    **decided)
                return Decision(outcome=REFUSED, rule=_rule_ref(default), warnings=warnings,
                                reason=f"the maker may not {self.label(self.item['checker_duty']).lower()} their own "
                                       f"{item_label.lower()}: another eligible checker exists in this organization",
                                **decided)
            return Decision(outcome=REFUSED, rule=_rule_ref(default or floor), warnings=warnings,
                            reason=f"the same person may not {self.label(self.item['maker_duty']).lower()} and "
                                   f"{self.label(self.item['checker_duty']).lower()} on the same "
                                   f"{item_label.lower()}", **decided)
        # not the maker: the org's own stricter rules
        for r in self.org_rules():
            hit = self._org_rule_hit(r, c_person, m_persons, related_makers or {}, enforce)
            if not hit:
                continue
            if r["severity"] == "block":
                return Decision(outcome=REFUSED, rule=_rule_ref(r), warnings=warnings,
                                reason=f"organization rule {r['rule_key']}: {r['description']}", **decided)
            warnings.append({"code": "org_rule_warn", "message": f"{r['rule_key']}: {r['description']}",
                             "rule": _rule_ref(r)})
        return Decision(outcome=ALLOWED, reason="allowed: the checker is not a maker of this item",
                        rule=None, warnings=warnings, **decided)

    def _org_rule_hit(self, r: dict, c_person: str, m_persons: list[str], related: dict, enforce: bool) -> bool:
        if r["scope"] == "same_item":
            extra = [self.person(u, enforce=enforce) for u in _ids(related.get(r["maker_duty"]) or [])]
            return c_person in extra
        if r["scope"] == "same_household":
            if self.household_id is None:
                return False
            days = r["period_days"]
            for h in self.household_history:
                if h["age_days"] > days:
                    continue
                if self.maker_duty_of.get(h["item_type"]) != r["maker_duty"]:
                    continue
                if c_person in (h["maker_persons"] or []):
                    return True
            return False
        if r["scope"] == "same_period":
            # reciprocal approval: the current maker checked an item THIS checker made, inside the period
            days = r["period_days"]
            for h in self.period_history:
                if h["age_days"] > days or h["item_type"] != self.item_type:
                    continue
                if h["checker_person"] in m_persons and c_person in (h["maker_persons"] or []):
                    return True
            return False
        return False


def _rule_ref(r: dict | None) -> dict | None:
    if not r:
        return None
    return {"id": r["id"], "rule_key": r["rule_key"], "rule_kind": r["rule_kind"],
            "owner_scope": "org" if r["org_id"] else "platform", "version_no": r["version_no"],
            "severity": r.get("severity"), "description": r.get("description")}


LOAD_SQL = """
WITH it AS (SELECT item_type, label, description, maker_duty, checker_duty, floor_class, self_approval_capable,
                   pre_s30_rule, site
              FROM public.sod_item_types WHERE item_type = $2),
rules AS (SELECT id::text AS id, rule_key, org_id::text AS org_id, rule_kind, maker_duty, checker_duty, scope,
                 period_days, severity, self_approval, enforce_floor, description, version_no
            FROM public.sod_rules
           WHERE item_type = $2 AND status = 'active' AND valid_to IS NULL AND (org_id IS NULL OR org_id = $1::uuid)
           ORDER BY rule_kind, rule_key),
ids AS (SELECT DISTINCT x AS id FROM unnest($3::uuid[]) AS x),
persons AS (
  SELECT ids.id::text AS id,
         COALESCE(pl.person_user_id, dm.user_id, ids.id)::text AS person,
         CASE WHEN pl.person_user_id IS NOT NULL THEN pl.linked_via
              WHEN dm.user_id IS NOT NULL THEN 'duplicate_merge' END AS via
    FROM ids
    LEFT JOIN public.user_person_links pl ON pl.user_id = ids.id
    LEFT JOIN public.users u ON u.id = ids.id
    LEFT JOIN LATERAL (SELECT ui.user_id FROM public.user_identities ui
                        WHERE u.status = 'retired' AND u.auth0_sub IS NOT NULL AND ui.auth0_sub = u.auth0_sub
                          AND ui.user_id <> u.id
                        ORDER BY ui.created_at LIMIT 1) dm ON true),
win AS (SELECT COALESCE(max(period_days), 0) AS days FROM rules WHERE rule_kind = 'org' AND scope <> 'same_item')
SELECT (SELECT row_to_json(it) FROM it) AS item,
       (SELECT COALESCE(json_agg(rules), '[]'::json) FROM rules) AS rules,
       (SELECT COALESCE(json_agg(persons), '[]'::json) FROM persons) AS persons,
       (SELECT COALESCE(json_object_agg(duty_key, json_build_object('label', label, 'kind', kind,
                                                                    'permissions', permissions)), '{}'::json)
          FROM public.sod_duties) AS duties,
       (SELECT COALESCE(json_object_agg(item_type, maker_duty), '{}'::json) FROM public.sod_item_types) AS maker_duty_of,
       (SELECT COALESCE(json_agg(h), '[]'::json) FROM (
           SELECT d.item_type, d.item_id, d.maker_persons::text[] AS maker_persons, d.checker_person::text AS checker_person,
                  EXTRACT(EPOCH FROM (now() - d.created_at)) / 86400.0 AS age_days
             FROM public.sod_decisions d, win
            WHERE $4::uuid IS NOT NULL AND win.days > 0 AND d.org_id = $1::uuid AND d.household_id = $4::uuid
              AND d.outcome <> 'refused' AND d.created_at > now() - make_interval(days => win.days)
            ORDER BY d.created_at DESC LIMIT 500) h) AS household_history,
       (SELECT COALESCE(json_agg(h), '[]'::json) FROM (
           SELECT d.item_type, d.item_id, d.maker_persons::text[] AS maker_persons, d.checker_person::text AS checker_person,
                  EXTRACT(EPOCH FROM (now() - d.created_at)) / 86400.0 AS age_days
             FROM public.sod_decisions d, win
            WHERE win.days > 0 AND EXISTS (SELECT 1 FROM rules WHERE rule_kind = 'org' AND scope = 'same_period')
              AND d.org_id = $1::uuid AND d.item_type = $2 AND d.outcome <> 'refused'
              AND d.created_at > now() - make_interval(days => win.days)
              AND (d.checker_person = ANY($3::uuid[]) OR d.maker_persons && $3::uuid[])
            ORDER BY d.created_at DESC LIMIT 500) h) AS period_history
"""


async def load(conn, *, org_id, item_type: str, ids: Iterable, household_id=None) -> Context:
    """The ONE read: the item type, its active rules (platform + this org), the person behind every id, the duty
    labels and — only when an org rule needs it — the household / reciprocal history."""
    if not item_type:
        raise SoDError("an item type is required")
    id_list = _ids(ids)
    hh = _ids([household_id])[0] if household_id is not None else None
    row = await conn.fetchrow(LOAD_SQL, str(org_id), item_type, id_list, hh)
    persons = {p["id"]: p for p in _loads(row["persons"], [])}
    return Context(org_id=str(org_id), item_type=item_type, item=_loads(row["item"], None),
                   rules=_loads(row["rules"], []), persons=persons, duties=_loads(row["duties"], {}),
                   maker_duty_of=_loads(row["maker_duty_of"], {}),
                   household_history=_loads(row["household_history"], []),
                   period_history=_loads(row["period_history"], []), household_id=hh)


async def can_check(conn, *, org_id, item_type: str, checker_id, maker_ids, item_id=None, household_id=None,
                    other_checker_exists: bool | None = None, related_makers: dict | None = None,
                    site: str | None = None) -> Decision:
    """May ``checker_id`` check this item, made by ``maker_ids``? Load + evaluate; writes nothing."""
    makers = _ids(maker_ids)
    rel = [u for v in (related_makers or {}).values() for u in (v or [])]
    ctx = await load(conn, org_id=org_id, item_type=item_type,
                     ids=[*([checker_id] if checker_id is not None else []), *makers, *rel],
                     household_id=household_id)
    return ctx.evaluate(checker_id=checker_id, maker_ids=makers, other_checker_exists=other_checker_exists,
                        related_makers=related_makers, item_id=item_id, site=site)


async def same_person(conn, org_id, a, b) -> bool:
    """Are two user ids the same PERSON (linked logins / merged rows)?"""
    if a is None or b is None:
        return False
    ctx = await load(conn, org_id=org_id, item_type=SOD_RULE, ids=[a, b])
    return ctx.person(_ids([a])[0]) == ctx.person(_ids([b])[0])


# ── Eligible checkers ───────────────────────────────────────────────────────────────────────────────────────────────
ELIGIBLE_SQL = """
SELECT DISTINCT ur.user_id::text AS user_id
  FROM public.user_roles ur
  JOIN public.roles r ON r.id = ur.role_id
  JOIN public.role_permissions rp ON rp.role_id = r.id
  JOIN public.permissions p ON p.id = rp.permission_id
 WHERE r.org_id = $1::uuid AND p.name = ANY($2::text[])
 ORDER BY 1
"""


async def checker_permissions(conn, item_type: str) -> list[str]:
    row = await conn.fetchrow("SELECT d.permissions FROM public.sod_item_types t JOIN public.sod_duties d "
                              "ON d.duty_key = t.checker_duty WHERE t.item_type = $1", item_type)
    return list(row["permissions"]) if row else []


async def eligible_checkers(conn, *, org_id, item_type: str, maker_ids, permissions: list[str] | None = None) -> list[str]:
    """The PEOPLE in ``org_id`` who could check this item: holders BY ROLE of the item type's checker-duty
    permission(s), every maker person excluded. Org-level holders only — never Super Admin (a platform escape hatch
    is not an org's own review capacity; selfapproval.structural). With the org's floor off for the item type, the
    maker is excluded by login id, exactly as before S30."""
    perms = permissions or await checker_permissions(conn, item_type)
    if not perms:
        return []
    holders = [r["user_id"] for r in await conn.fetch(ELIGIBLE_SQL, str(org_id), perms)]
    makers = _ids(maker_ids)
    ctx = await load(conn, org_id=org_id, item_type=item_type, ids=[*holders, *makers])
    enforce = ctx.policy()["enforce_floor"]
    maker_persons = {ctx.person(m, enforce=enforce) for m in makers}
    out: list[str] = []
    for h in holders:
        p = ctx.person(h, enforce=enforce)
        if p in maker_persons or (not enforce and h in makers):
            continue
        if p not in out:
            out.append(p)
    return out


# ── Recording ───────────────────────────────────────────────────────────────────────────────────────────────────────
INSERT_DECISION_SQL = """
INSERT INTO public.sod_decisions (org_id, item_type, item_id, household_id, site, checker_id, checker_person,
                                  maker_ids, maker_persons, outcome, rule_id, rule_key, reason, disclosure, warnings)
VALUES ($1::uuid, $2, $3, $4::uuid, $5, $6::uuid, $7::uuid, $8::uuid[], $9::uuid[], $10, $11::uuid, $12, $13, $14,
        $15::jsonb)
RETURNING id::text
"""


def _decision_args(d: Decision, site: str | None):
    rule = d.rule or {}
    return (d.org_id, d.item_type, d.item_id, d.household_id, site or d.site or "unknown", d.checker_id,
            d.checker_person, d.maker_ids, d.maker_persons, OUTCOME_RECORDED[d.outcome], rule.get("id"),
            rule.get("rule_key"), d.reason, d.disclosure, json.dumps(d.warnings, default=str))


async def record_decision(conn, d: Decision, *, site: str | None = None) -> str | None:
    """Write the audit row for a real decision on ``conn`` (inside the caller's transaction: it commits with the
    approval it records). A self-approval also raises the compliance to-do."""
    if d.item_type is None or d.outcome not in OUTCOME_RECORDED:
        raise SoDError("not a decision")
    if d.self_approval:
        # a self-approval is NEVER silent: its record and its compliance to-do commit with it, or nothing does
        did = await conn.fetchval(INSERT_DECISION_SQL, *_decision_args(d, site))
        d.decision_id = did
        await raise_self_approval_todo(conn, d)
        return did
    try:
        async with conn.transaction():   # a savepoint: a failed log write never aborts the approval it records
            did = await conn.fetchval(INSERT_DECISION_SQL, *_decision_args(d, site))
    except Exception:  # noqa: BLE001
        logger.exception("sod: could not record an allowed check (%s %s)", d.item_type, d.item_id)
        return None
    d.decision_id = did
    return did


async def record_refusal(d: Decision, *, site: str | None = None) -> str | None:
    """A refusal is recorded on a FRESH pooled connection (the caller is about to raise, rolling its own transaction
    back). Never raises — a refusal is never masked by a failure to log it; the failure is logged instead."""
    try:
        from services.database import get_pool

        pool = await get_pool()
        async with pool.acquire() as c:
            did = await c.fetchval(INSERT_DECISION_SQL, *_decision_args(d, site))
            # S29B Task 0: every refusal is in the audit log too (the decision row is the append-only record; the audit
            # row puts it beside every other security event). Same connection, same commit.
            rule = d.rule or {}
            await c.execute(
                "INSERT INTO audit_log (org_id, user_id, action, resource_type, resource_id, payload) "
                "VALUES ($1::uuid, $2::uuid, 'sod.refused', 'sod_decisions', $3::uuid, $4::jsonb)",
                d.org_id, d.checker_id, did,
                json.dumps({"item_type": d.item_type, "item_id": d.item_id, "site": site or d.site,
                            "maker_ids": d.maker_ids, "rule_key": rule.get("rule_key"), "reason": d.reason},
                           default=str))
        d.decision_id = did
        return did
    except Exception:  # noqa: BLE001
        logger.exception("sod: could not record a refused check (%s %s)", d.item_type, d.item_id)
        return None


RECORD_ALL = "all"            # the decision layer: allowed, self-approved and refused checks are recorded
RECORD_REFUSALS = "refusals"  # an inner gate (a pre-check, an apply callback): only a refusal is recorded


async def enforce(conn, *, org_id, item_type: str, checker_id, maker_ids, item_id=None, household_id=None,
                  other_checker_exists: bool | None = None, related_makers: dict | None = None,
                  site: str, refuse: Callable[[Decision], Exception] | None = None,
                  record: str | bool = RECORD_ALL) -> Decision:
    """``can_check`` + record + raise. ``refuse(decision)`` builds the SITE's own exception (its own class, and the
    refusal text that names who made it — services.maker_names); a refusal is recorded, then raised.

    ``record``: RECORD_ALL at the layer that DECIDES (an allowed / self-approved check is written on ``conn``, so
    it commits with the approval); RECORD_REFUSALS at an inner gate that re-checks what the decision layer already
    recorded (one row per real decision, a refusal wherever it happens); False writes nothing."""
    d = await can_check(conn, org_id=org_id, item_type=item_type, checker_id=checker_id, maker_ids=maker_ids,
                        item_id=item_id, household_id=household_id, other_checker_exists=other_checker_exists,
                        related_makers=related_makers, site=site)
    if d.refused:
        if record:
            await record_refusal(d, site=site)
        raise (refuse(d) if refuse else SoDRefused(d))
    if record == RECORD_ALL or record is True:
        await record_decision(conn, d, site=site)
    return d


def may_check_display(user_id, maker_ids, *, ctx: "Context | None" = None) -> bool:
    """The rule a SCREEN uses to offer Approve / Publish (advisory — the server re-checks through ``enforce``).

    With a loaded ``ctx`` it is the full evaluation (persons, floor, org rules; a governed self-approval counts as
    "may", the decision layer then records the disclosure). Without one it is the login-id comparison every screen
    used before S30 — never a second rule, only the same rule without its one read."""
    if user_id is None:
        return False
    makers = [str(m) for m in (maker_ids or ()) if m is not None]
    if ctx is not None:
        try:
            return ctx.evaluate(checker_id=user_id, maker_ids=makers, other_checker_exists=False).allowed
        except SoDError:
            return False
    return str(user_id) not in makers


async def raise_self_approval_todo(conn, d: Decision) -> list:
    """Every self-approval raises a compliance to-do: holders of manage_compliance in the org, plus its org admins,
    EXCEPT the self-approver (a review of your own self-approval is no review) — the self-approver only when nobody
    else exists, so the to-do is never dropped. Keyed on the decision id."""
    from services.workflow_todos import _org_admin_recipients, _upsert_todo
    from services.rbac import get_users_with_permission
    from services.database import get_pool

    pool = await get_pool()
    recipients = set(await get_users_with_permission(pool, d.org_id, "manage_compliance"))
    try:
        recipients |= set(await _org_admin_recipients(d.org_id))
    except Exception:  # noqa: BLE001
        logger.exception("sod: org admin recipients unavailable")
    recipients = {str(r) for r in recipients} - {str(d.checker_id), str(d.checker_person)}
    if not recipients and d.checker_id:
        recipients = {str(d.checker_id)}
    ids = []
    for uid in sorted(recipients):
        ids.append(await _upsert_todo(
            conn, org_id=d.org_id, user_id=uid, source=TODO_SOURCE_SELF_APPROVAL, related_type="sod_decisions",
            related_id=d.decision_id, title="Review a self-approval",
            detail=f"{d.disclosure} Item: {d.item_type} {d.item_id or ''}".strip(), priority=3,
            action_key="/admin/sod?tab=self_approvals"))
    return ids


# ── Workflow verbs (Task 8) ─────────────────────────────────────────────────────────────────────────────────────────
# Every registered action declares its duty: 'read', 'propose', 'approve' or 'write'. A WRITE verb without a declared
# duty is refused at validation (workflow_nl_generator._validate) — S29b (autonomous write verbs) relies on it.
VERB_DUTY_KINDS = ("read", "propose", "approve", "write")
VERB_DUTIES: dict[str, str] = {
    "ai.drift_monitor": "write",
    "crm.draft_note": "propose",
    "edgar.launch_pipeline_job": "write",
    "entity.link_ownership": "write",
    "litellm.reload_model_cost_map": "write",
    "propose": "propose",
    "security_data.nightly": "write",
    "security_data.prices": "write",
    "security_master.attributes": "write",
    "security_master.mutual_funds": "write",
    "security_master.openfigi_new": "write",
    "security_master.sec13f": "write",
    "security_master.universe": "write",
    "spv.record_transaction": "write",
    "spv.subscribe": "write",
    "spv_carry.propose_from_realization": "propose",
    "cash.maturity_waterfall": "propose",     # CASH04 — drafts a cash proposal; a person adopts, another approves
    # CASH07 — payment instructions: the assistant may DRAFT at most (declared, NOT registered as an action in stage A);
    # submit / approve / send are a person's only (services.workflow_autonomy declares all four NEVER — money)
    "cash.payment_draft": "propose",
    "cash.payment_submit": "write",
    "cash.payment_approve": "approve",
    "cash.payment_send": "write",
    # CASH06 — a cash rule's firing DRAFTS + SUBMITS payment instructions for a person's approval (nothing executes
    # until a different person approves; the custody cliff is unchanged): 'propose'. Engine-only (workflow_only).
    "cash.rule_fire": "propose",
}


def verb_duty(action) -> str | None:
    """The declared duty of a registered action: its own ``duty`` field, else the central map; a READ action is
    'read' by definition. None → undeclared (a write verb without a duty)."""
    if action is None:
        return None
    declared = getattr(action, "duty", None) or VERB_DUTIES.get(getattr(action, "key", None) or "")
    if declared:
        return declared
    if getattr(action, "access_type", None) == "read":
        return "read"
    return None


def undeclared_write_verbs(registry) -> list[str]:
    return sorted(a.key for a in registry.all() if a.access_type == "write" and verb_duty(a) is None)


async def gate(conn, *, org_id, item_type: str, checker_id, maker_ids, site: str,
               refuse: Callable[[Decision], Exception], item_id=None, household_id=None,
               related_makers: dict | None = None) -> Decision:
    """An INNER gate (a module's pre-check before services.human_tasks decides, or its apply callback): the same
    rule as ``enforce``, a refusal recorded, an allowed check left for the decision layer to record."""
    return await enforce(conn, org_id=org_id, item_type=item_type, checker_id=checker_id, maker_ids=maker_ids,
                         item_id=item_id, household_id=household_id, related_makers=related_makers, site=site,
                         refuse=refuse, record=RECORD_REFUSALS)
