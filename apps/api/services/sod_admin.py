"""S30 — segregation of duties: role-grant checks, conflict report, rule editing (maker-checker) and reports.

The RULE itself lives in ``services.sod`` (one service); this module is everything around it:

* ``check_role_grant`` — Task 6: a grant of a role / permission set to a person is checked against the floor
  (granting YOURSELF is a floor conflict — blocked once the org's ``enforce_floor`` is on for role grants; the
  platform default is OFF because before S30 an admin could change their own role: reported, never silently
  tightened), and the person's resulting duties are matched against the matrix: a combination that lets one person
  both MAKE and CHECK the same item type is a WARNING naming the rules that will still stop them per item.
* ``conflict_report`` — every person in the org whose effective permissions combine a maker and checker duty.
* rules — an org admin drafts an org rule / policy, a DIFFERENT person decides it (maker-checker, through
  ``services.sod`` itself as item type ``sod_rule``); a Super Admin drafts a platform rule, a DIFFERENT Super Admin
  decides it (``sod_platform_rule``). Every version kept (valid-time axis). An org can never RELAX a floor: a policy
  turning self-approval on for an item type that cannot self-approve, or turning ``enforce_floor`` off where the
  platform has it on, is refused before anything is written.
* reports — duties, item types, rules (floor / default / policy / org, with history), conflicts, self-approvals,
  refused checks (last N days) — each also as CSV.
"""

from __future__ import annotations

import csv
import io
import json
import re
from typing import Any

from services import sod
from services.authz import PRINCIPAL_COLUMNS, principal_from_row
from services import rbac

READ_PERMISSIONS = ("manage_org_settings", "manage_compliance", "view_audit_log")
WRITE_PERMISSION = "manage_org_settings"
ORG_RULE_SCOPES = ("same_item", "same_household", "same_period")
REPORTS = ("duties", "item_types", "rules", "conflicts", "self_approvals", "refusals")


class SoDAdminError(Exception):
    """A bad request (400)."""


class SoDAdminConflict(Exception):
    """A state conflict (409)."""


class SoDAdminRefused(Exception):
    """A refusal by rule (403) — maker-checker, a floor, or a relaxation attempt."""

    def __init__(self, message: str, decision: sod.Decision | None = None):
        super().__init__(message)
        self.decision = decision


def _s(v):
    return None if v is None else str(v)


def _iso(v):
    return v.isoformat() if v is not None else None


def _j(v, default):
    if v is None:
        return default
    if isinstance(v, (dict, list)):
        return v
    return json.loads(v)


# ── Catalog ─────────────────────────────────────────────────────────────────────────────────────────────────────────
async def duties(conn) -> list[dict]:
    return [dict(r) for r in await conn.fetch(
        "SELECT duty_key, label, description, kind, permissions, sort_order FROM public.sod_duties "
        "ORDER BY sort_order, duty_key")]


async def item_types(conn) -> list[dict]:
    return [dict(r) for r in await conn.fetch(
        "SELECT item_type, label, description, maker_duty, checker_duty, floor_class, self_approval_capable, "
        "pre_s30_rule, site, sort_order FROM public.sod_item_types ORDER BY sort_order, item_type")]


def duties_held(permissions: frozenset | set, is_super_admin: bool, duty_rows: list[dict]) -> set[str]:
    """A duty is held when ANY permission that grants it is held (Super Admin holds every duty that has one). A duty
    granted by no permission (receive_role, publish_template, open/decide task) is never 'held' by permission."""
    held = set()
    for d in duty_rows:
        perms = set(d["permissions"] or [])
        if not perms:
            continue
        if is_super_admin or perms & set(permissions):
            held.add(d["duty_key"])
    return held


def conflicts_for(held: set[str], types: list[dict], rules_by_type: dict[str, list[dict]]) -> list[dict]:
    """Item types where the person holds BOTH the maker duty and the checker duty — each with the rules that will
    still stop them on any one item (the floor / default rule, plus the org's own)."""
    out = []
    for t in types:
        if t["maker_duty"] in held and t["checker_duty"] in held:
            stoppers = [r for r in rules_by_type.get(t["item_type"], []) if r["rule_kind"] in ("floor", "default", "org")]
            out.append({"item_type": t["item_type"], "label": t["label"], "maker_duty": t["maker_duty"],
                        "checker_duty": t["checker_duty"], "floor_class": t["floor_class"],
                        "severity": "warn",
                        "still_stopped_by": [{"rule_key": r["rule_key"], "rule_kind": r["rule_kind"],
                                              "description": r["description"]} for r in stoppers]})
    return out


async def _active_rules_by_type(conn, org_id) -> dict[str, list[dict]]:
    rows = await conn.fetch(
        "SELECT id::text AS id, rule_key, org_id::text AS org_id, rule_kind, item_type, description, severity, scope "
        "FROM public.sod_rules WHERE status = 'active' AND valid_to IS NULL AND (org_id IS NULL OR org_id = $1::uuid) "
        "ORDER BY rule_kind, rule_key", str(org_id))
    out: dict[str, list[dict]] = {}
    for r in rows:
        out.setdefault(r["item_type"], []).append(dict(r))
    return out


# ── Task 6: role / permission-set grants ────────────────────────────────────────────────────────────────────────────
#: every path that grants a person a role or permissions, by its site key (docs/S30_DISCOVERY.md 1a) → the words the
#: refusal uses. s30 fix 1: the key was f"role_grant:{what}" with free text ("permission set" carried a space).
GRANT_SITES = {
    "role": "role_grant:role",                                   # PUT  /admin/users/{id}/role; approve pending
    "permission set": "role_grant:permission_set",               # POST /admin/permission-sets/{id}/users
    "profile": "role_grant:profile",                             # PUT  /admin/users/{id}/profile
    "profile permission": "role_grant:profile_permission",       # PUT  /admin/profiles/{id}/permissions
    "permission set permission": "role_grant:permission_set_permission",  # PUT /admin/permission-sets/{id}/permissions
}


async def check_role_grant(conn, *, org_id, actor_id, target_user_id, what: str, record: bool = True) -> sod.Decision:
    """Refuse a self-grant once the floor is enforced for role grants (Super Admin included); record the decision.
    Raises SoDAdminRefused on a refusal. ``what`` is a key of GRANT_SITES."""
    site = GRANT_SITES[what]
    d = await sod.can_check(conn, org_id=org_id, item_type=sod.ROLE_GRANT, checker_id=target_user_id,
                            maker_ids=[actor_id], item_id=target_user_id, site=site)
    if d.refused:
        if record:
            await sod.record_refusal(d, site=site)
        raise SoDAdminRefused(f"Segregation of duties: you cannot grant yourself a {what}. {d.reason}.", d)
    if record:
        await sod.record_decision(conn, d, site=site)
    return d


async def check_bundle_edit(conn, *, org_id, actor_id, holder_ids, what: str) -> sod.Decision | None:
    """Adding a permission to a profile / permission set grants it to everyone holding that bundle — so when the
    ACTOR (as a person: any of their logins) is one of the holders, the edit is a self-grant and goes through
    check_role_grant. Returns None when the actor holds nothing of it (no grant to themselves)."""
    holders = [str(h) for h in holder_ids if h is not None]
    if not holders:
        return None
    ctx = await sod.load(conn, org_id=org_id, item_type=sod.ROLE_GRANT, ids=[actor_id, *holders])
    me = ctx.person(str(actor_id))
    mine = next((h for h in holders if ctx.person(h) == me), None)
    if mine is None:
        return None
    return await check_role_grant(conn, org_id=org_id, actor_id=actor_id, target_user_id=mine, what=what)


async def person_conflicts(conn, *, org_id, user_id) -> list[dict]:
    """The conflicting duty combinations a person holds NOW (read after a grant, on the grant's connection)."""
    row = await conn.fetchrow(f"SELECT {PRINCIPAL_COLUMNS} FROM users u WHERE u.id = $1::uuid",
                              str(user_id), rbac.SUPER_ADMIN_ROLE)
    p = principal_from_row(row)
    if p is None or not p.admitted:
        return []
    duty_rows = await duties(conn)
    held = duties_held(p.permissions, p.is_super_admin, duty_rows)
    return conflicts_for(held, await item_types(conn), await _active_rules_by_type(conn, org_id))


def grant_warnings(decision: sod.Decision | None, conflicts: list[dict]) -> list[dict]:
    out = list(decision.warnings if decision else [])
    for c in conflicts:
        out.append({"code": "conflicting_duties", "item_type": c["item_type"],
                    "message": f"This person can both {c['maker_duty'].replace('_', ' ')} and "
                               f"{c['checker_duty'].replace('_', ' ')} for {c['label'].lower()} items. "
                               "They are still stopped on any one item by: "
                               + "; ".join(r["rule_key"] for r in c["still_stopped_by"]) + ".",
                    "still_stopped_by": c["still_stopped_by"]})
    return out


async def conflict_report(conn, *, org_id) -> list[dict]:
    """Every admitted person in the org whose effective permissions combine a maker and a checker duty.

    Super Admins (s30 fix 1, the stated rule): a Super Admin holds EVERY duty inherently, so they are always listed —
    never hidden (that would understate who can make and check) — but reported as their own group: ``all_duties``
    true, after the ordinary people, so they don't drown out the person whose ROLES grant the conflict. Their
    per-item ``conflicts`` are still returned (the CSV carries them); the floor / default rules stop them per item
    exactly as anyone else."""
    rows = await conn.fetch(f"SELECT {PRINCIPAL_COLUMNS}, u.full_name FROM users u WHERE u.org_id = $1::uuid "
                            "ORDER BY u.full_name NULLS LAST, u.email", str(org_id), rbac.SUPER_ADMIN_ROLE)
    duty_rows = await duties(conn)
    types = await item_types(conn)
    rules = await _active_rules_by_type(conn, org_id)
    out = []
    for r in rows:
        p = principal_from_row(r)
        if p is None or not p.admitted:
            continue
        cs = conflicts_for(duties_held(p.permissions, p.is_super_admin, duty_rows), types, rules)
        if cs:
            out.append({"user_id": p.user_id, "name": r["full_name"] or p.email, "email": p.email,
                        "roles": list(p.roles), "is_super_admin": p.is_super_admin,
                        "all_duties": bool(p.is_super_admin),
                        "conflicts": cs, "conflict_count": len(cs)})
    out.sort(key=lambda x: x["all_duties"])   # stable: ordinary people first (by name), then Super Admins
    return out


# ── Rules ───────────────────────────────────────────────────────────────────────────────────────────────────────────
RULE_COLS = ("id::text AS id, rule_key, owner_scope, org_id::text AS org_id, rule_kind, item_type, maker_duty, "
             "checker_duty, scope, period_days, severity, self_approval, enforce_floor, description, retires, status, "
             "version_no, drafted_by::text AS drafted_by, checker_id::text AS checker_id, decided_at, decision_note, "
             "valid_from, valid_to, created_at")


def _rule_out(r) -> dict:
    d = dict(r)
    for k in ("decided_at", "valid_from", "valid_to", "created_at"):
        d[k] = _iso(d.get(k))
    return d


async def list_rules(conn, *, org_id, include_history: bool = True) -> list[dict]:
    sql = (f"SELECT {RULE_COLS} FROM public.sod_rules WHERE (org_id IS NULL OR org_id = $1::uuid)"
           + ("" if include_history else " AND status IN ('active', 'draft') AND valid_to IS NULL")
           + " ORDER BY item_type, rule_kind, rule_key, version_no, created_at")
    return [_rule_out(r) for r in await conn.fetch(sql, str(org_id))]


async def _load_rule(conn, rule_id, *, lock: bool = False):
    try:
        rid = str(__import__("uuid").UUID(str(rule_id)))
    except (TypeError, ValueError):
        raise SoDAdminError("not a rule id") from None
    r = await conn.fetchrow(f"SELECT {RULE_COLS} FROM public.sod_rules WHERE id = $1::uuid"
                            + (" FOR UPDATE" if lock else ""), rid)
    if r is None:
        raise SoDAdminConflict("rule not found")
    return r


def _key_ok(k: str) -> bool:
    return bool(re.fullmatch(r"[a-z][a-z0-9_:.]{2,80}", k or ""))


async def draft_rule(conn, *, org_id, actor_id, is_super_admin: bool, body: dict) -> dict:
    """Draft a new version of a rule. ``body``: owner_scope ('org' | 'platform'), rule_kind ('org' | 'policy' |
    'floor' | 'default'), item_type, and — for an org rule — maker_duty, scope, period_days, severity; for a policy —
    self_approval, enforce_floor; description; retires (org rule only). Refuses any RELAXATION of a floor."""
    scope_owner = body.get("owner_scope") or "org"
    kind = body.get("rule_kind")
    item_type = body.get("item_type")
    types = {t["item_type"]: t for t in await item_types(conn)}
    t = types.get(item_type)
    if t is None:
        raise SoDAdminError(f"unknown item type {item_type!r}")
    if scope_owner == "platform":
        if not is_super_admin:
            raise SoDAdminRefused("only a Super Admin drafts a platform rule")
        if kind not in ("floor", "default", "policy"):
            raise SoDAdminError("a platform rule is a floor, default or policy rule")
        target_org = None
    elif scope_owner == "org":
        if kind not in ("org", "policy"):
            raise SoDAdminError("an organization drafts its own rules or its policy — never a floor or default rule")
        target_org = str(org_id)
    else:
        raise SoDAdminError("owner_scope is org or platform")
    description = (body.get("description") or "").strip()
    if not description:
        raise SoDAdminError("a rule needs a description")

    plat_policy = await conn.fetchrow(
        "SELECT self_approval, enforce_floor FROM public.sod_rules WHERE org_id IS NULL AND rule_kind = 'policy' "
        "AND item_type = $1 AND status = 'active' AND valid_to IS NULL", item_type)
    fields: dict[str, Any] = {"maker_duty": None, "checker_duty": None, "scope": "same_item", "period_days": None,
                              "severity": "block", "self_approval": None, "enforce_floor": None,
                              "retires": bool(body.get("retires"))}
    if kind == "policy":
        sa = body.get("self_approval")
        ef = body.get("enforce_floor")
        if sa not in ("never", "when_no_other_checker") or not isinstance(ef, bool):
            raise SoDAdminError("a policy sets self_approval ('never' | 'when_no_other_checker') and enforce_floor")
        if sa == "when_no_other_checker" and (t["floor_class"] or not t["self_approval_capable"]):
            raise SoDAdminRefused(
                f"{t['label']} can never be self-approved ("
                + ("a floor item type" if t["floor_class"] else "its record cannot carry a disclosed self-approval")
                + ") — a policy cannot relax the floor")
        if scope_owner == "org" and plat_policy is not None and plat_policy["enforce_floor"] and not ef:
            raise SoDAdminRefused(f"the platform enforces the floor for {t['label'].lower()} — an organization "
                                  "cannot turn it off")
        fields.update(self_approval=sa, enforce_floor=ef)
        rule_key = f"policy:{item_type}"
    elif kind == "org":
        scope = body.get("scope") or "same_item"
        if scope not in ORG_RULE_SCOPES:
            raise SoDAdminError(f"scope is one of {ORG_RULE_SCOPES}")
        maker_duty = body.get("maker_duty") or t["maker_duty"]
        duty_keys = {d["duty_key"] for d in await duties(conn)}
        if maker_duty not in duty_keys:
            raise SoDAdminError(f"unknown duty {maker_duty!r}")
        period = body.get("period_days")
        if scope == "same_item":
            if period is not None:
                raise SoDAdminError("a same-item rule has no period")
        elif not isinstance(period, int) or not 1 <= period <= 3660:
            raise SoDAdminError("a household / period rule needs period_days between 1 and 3660")
        severity = body.get("severity") or "block"
        if severity not in ("block", "warn"):
            raise SoDAdminError("severity is block or warn")
        fields.update(maker_duty=maker_duty, checker_duty=t["checker_duty"], scope=scope, period_days=period,
                      severity=severity)
        rule_key = body.get("rule_key") or f"org:{item_type}:{scope}:{maker_duty}"
        if not _key_ok(rule_key):
            raise SoDAdminError("rule_key: lower-case letters, digits, _ : . (3–80)")
    else:  # platform floor / default
        if fields["retires"]:
            raise SoDAdminError("a floor or default rule is never retired")
        if kind == "floor" and not t["floor_class"]:
            raise SoDAdminError(f"{t['label']} is not a floor item type")
        if kind == "default" and t["floor_class"]:
            raise SoDAdminError(f"{t['label']} is a floor item type — its rule is a floor rule")
        fields.update(maker_duty=t["maker_duty"], checker_duty=t["checker_duty"])
        rule_key = f"{kind}:{item_type}"

    if fields["retires"] and kind != "org":
        raise SoDAdminError("only an organization rule is retired")
    current = await conn.fetchrow(
        "SELECT id, version_no FROM public.sod_rules WHERE rule_key = $1 AND COALESCE(org_id, "
        "'00000000-0000-0000-0000-000000000000'::uuid) = COALESCE($2::uuid, '00000000-0000-0000-0000-000000000000'::uuid) "
        "AND status = 'active' AND valid_to IS NULL", rule_key, target_org)
    if fields["retires"] and current is None:
        raise SoDAdminConflict(f"no active rule {rule_key} to retire")
    draft = await conn.fetchval(
        "SELECT 1 FROM public.sod_rules WHERE rule_key = $1 AND COALESCE(org_id, "
        "'00000000-0000-0000-0000-000000000000'::uuid) = COALESCE($2::uuid, '00000000-0000-0000-0000-000000000000'::uuid) "
        "AND status = 'draft'", rule_key, target_org)
    if draft:
        raise SoDAdminConflict(f"a draft of {rule_key} is already awaiting a decision")
    version_no = (current["version_no"] + (0 if fields["retires"] else 1)) if current else 1
    row = await conn.fetchrow(
        f"""INSERT INTO public.sod_rules (rule_key, owner_scope, org_id, rule_kind, item_type, maker_duty, checker_duty,
                scope, period_days, severity, self_approval, enforce_floor, description, retires, status, version_no,
                drafted_by)
            VALUES ($1, $2, $3::uuid, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, 'draft', $15, $16::uuid)
            RETURNING {RULE_COLS}""",
        rule_key, scope_owner, target_org, kind, item_type, fields["maker_duty"], fields["checker_duty"],
        fields["scope"], fields["period_days"], fields["severity"], fields["self_approval"], fields["enforce_floor"],
        description, fields["retires"], version_no, str(actor_id))
    await _audit(conn, org_id, actor_id, "sod.rule_drafted", row["id"],
                 {"rule_key": rule_key, "owner_scope": scope_owner, "version_no": version_no})
    return _rule_out(row)


async def decide_rule(conn, *, org_id, actor_id, is_super_admin: bool, rule_id, outcome: str,
                      note: str | None = None) -> dict:
    """A DIFFERENT person decides a draft (maker-checker through services.sod itself). Approving activates the draft
    and closes the version it replaces (valid_to); approving a retirement closes the active version."""
    if outcome not in ("approve", "reject"):
        raise SoDAdminError("outcome is approve or reject")
    if outcome == "reject" and not (note or "").strip():
        raise SoDAdminError("a rejection needs a note saying why")
    async with conn.transaction():
        r = await _load_rule(conn, rule_id, lock=True)
        if r["status"] != "draft":
            raise SoDAdminConflict(f"this rule version is {r['status']}, not a draft")
        platform = r["org_id"] is None
        if platform and not is_super_admin:
            raise SoDAdminRefused("only a Super Admin decides a platform rule")
        if not platform and r["org_id"] != str(org_id):
            raise SoDAdminConflict("rule not found")
        await sod.enforce(conn, org_id=org_id, item_type=sod.SOD_PLATFORM_RULE if platform else sod.SOD_RULE,
                          checker_id=actor_id, maker_ids=[r["drafted_by"]], item_id=r["id"],
                          site="sod_admin.decide_rule",
                          refuse=lambda d: SoDAdminRefused(
                              "maker-checker: you drafted this rule version, so a different "
                              + ("Super Admin" if platform else "administrator") + f" must decide it ({d.reason})", d))
        ts = await conn.fetchval("SELECT clock_timestamp()")
        if outcome == "reject":
            new = await conn.fetchrow(
                f"UPDATE public.sod_rules SET status = 'rejected', checker_id = $2::uuid, decided_at = $3, "
                f"decision_note = $4 WHERE id = $1::uuid RETURNING {RULE_COLS}", r["id"], str(actor_id), ts, note)
        else:
            cur = await conn.fetchrow(
                "SELECT id FROM public.sod_rules WHERE rule_key = $1 AND COALESCE(org_id, "
                "'00000000-0000-0000-0000-000000000000'::uuid) = COALESCE($2::uuid, "
                "'00000000-0000-0000-0000-000000000000'::uuid) AND status = 'active' AND valid_to IS NULL FOR UPDATE",
                r["rule_key"], r["org_id"])
            if cur is not None:
                await conn.execute("UPDATE public.sod_rules SET status = 'retired', valid_to = $2 WHERE id = $1",
                                   cur["id"], ts)
            elif r["retires"]:
                raise SoDAdminConflict("the rule this draft retires is no longer active")
            new = await conn.fetchrow(
                # explicit casts: inside the CASE an untyped $3 is deduced as text, which conflicts with
                # decided_at's timestamptz (AmbiguousParameterError → a 500 on every approval; s30 fix 1)
                f"UPDATE public.sod_rules SET status = $5::text, checker_id = $2::uuid, decided_at = $3::timestamptz, "
                f"decision_note = $4::text, valid_from = valid_from, "
                f"valid_to = CASE WHEN $5::text = 'retired' THEN $3::timestamptz ELSE NULL END "
                f"WHERE id = $1::uuid RETURNING {RULE_COLS}",
                r["id"], str(actor_id), ts, note, "retired" if r["retires"] else "active")
        await _audit(conn, org_id, actor_id, f"sod.rule_{'approved' if outcome == 'approve' else 'rejected'}",
                     r["id"], {"rule_key": r["rule_key"], "version_no": r["version_no"], "retires": r["retires"]})
    return _rule_out(new)


async def _audit(conn, org_id, user_id, action: str, resource_id, payload: dict) -> None:
    await conn.execute(
        "INSERT INTO audit_log (org_id, user_id, action, resource_type, resource_id, payload) "
        "VALUES ($1::uuid, $2::uuid, $3, 'sod_rules', $4::uuid, $5::jsonb)",
        str(org_id), _s(user_id), action, str(resource_id), json.dumps(payload, default=str))


# ── Reports ─────────────────────────────────────────────────────────────────────────────────────────────────────────
DECISION_COLS = ("d.id::text AS id, d.item_type, t.label AS item_label, d.item_id, d.household_id::text AS household_id, "
                 "d.site, d.checker_id::text AS checker_id, d.checker_person::text AS checker_person, "
                 "d.maker_ids::text[] AS maker_ids, d.maker_persons::text[] AS maker_persons, d.outcome, d.rule_key, "
                 "d.reason, d.disclosure, d.warnings, d.created_at, u.full_name AS checker_name, u.email AS checker_email")


def _decision_out(r) -> dict:
    d = dict(r)
    d["created_at"] = _iso(d["created_at"])
    d["warnings"] = _j(d.get("warnings"), [])
    return d


async def self_approvals(conn, *, org_id, limit: int = 500) -> list[dict]:
    return [_decision_out(r) for r in await conn.fetch(
        f"SELECT {DECISION_COLS} FROM public.sod_decisions d JOIN public.sod_item_types t ON t.item_type = d.item_type "
        "LEFT JOIN users u ON u.id = d.checker_id WHERE d.org_id = $1::uuid AND d.outcome = 'self_approved' "
        "ORDER BY d.created_at DESC LIMIT $2", str(org_id), max(1, min(int(limit), 5000)))]


async def refusals(conn, *, org_id, days: int = 90, limit: int = 1000) -> list[dict]:
    days = max(1, min(int(days), 3660))
    return [_decision_out(r) for r in await conn.fetch(
        f"SELECT {DECISION_COLS} FROM public.sod_decisions d JOIN public.sod_item_types t ON t.item_type = d.item_type "
        "LEFT JOIN users u ON u.id = d.checker_id WHERE d.org_id = $1::uuid AND d.outcome = 'refused' "
        "AND d.created_at > now() - make_interval(days => $2) ORDER BY d.created_at DESC LIMIT $3",
        str(org_id), days, max(1, min(int(limit), 5000)))]


async def report(conn, name: str, *, org_id, days: int = 90) -> list[dict]:
    if name == "duties":
        return await duties(conn)
    if name == "item_types":
        return await item_types(conn)
    if name == "rules":
        return await list_rules(conn, org_id=org_id)
    if name == "conflicts":
        flat = []
        for p in await conflict_report(conn, org_id=org_id):
            for c in p["conflicts"]:
                flat.append({"user_id": p["user_id"], "name": p["name"], "email": p["email"],
                             "roles": ", ".join(p["roles"]), "item_type": c["item_type"], "label": c["label"],
                             "maker_duty": c["maker_duty"], "checker_duty": c["checker_duty"],
                             "still_stopped_by": ", ".join(r["rule_key"] for r in c["still_stopped_by"])})
        return flat
    if name == "self_approvals":
        return await self_approvals(conn, org_id=org_id)
    if name == "refusals":
        return await refusals(conn, org_id=org_id, days=days)
    raise SoDAdminError(f"unknown report {name!r} (one of {REPORTS})")


def to_csv(rows: list[dict]) -> str:
    buf = io.StringIO()
    if not rows:
        return ""
    cols = list(rows[0].keys())
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(cols)
    for r in rows:
        w.writerow([json.dumps(r[c], default=str) if isinstance(r.get(c), (list, dict)) else
                    ("" if r.get(c) is None else r.get(c)) for c in cols])
    return buf.getvalue()


async def overview(conn, *, org_id) -> dict:
    """The page's one read: catalog, rules (with history), conflicts, self-approvals, refusals (90 days)."""
    pols = {r["item_type"]: r for r in await conn.fetch(
        "SELECT DISTINCT ON (item_type) item_type, self_approval, enforce_floor, org_id IS NOT NULL AS org_override "
        "FROM public.sod_rules WHERE rule_kind = 'policy' AND status = 'active' AND valid_to IS NULL AND "
        "(org_id IS NULL OR org_id = $1::uuid) ORDER BY item_type, (org_id IS NOT NULL) DESC", str(org_id))}
    types = await item_types(conn)
    for t in types:
        p = pols.get(t["item_type"])
        t["policy"] = {"self_approval": p["self_approval"], "enforce_floor": p["enforce_floor"],
                       "source": "org" if p["org_override"] else "platform"} if p else None
    return {"duties": await duties(conn), "item_types": types, "rules": await list_rules(conn, org_id=org_id),
            "conflicts": await conflict_report(conn, org_id=org_id),
            "self_approvals": await self_approvals(conn, org_id=org_id),
            "refusals": await refusals(conn, org_id=org_id, days=90)}
