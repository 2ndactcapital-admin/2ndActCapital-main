"""WFTEMPLATES1 — the workflow template library (Workflow Manager Wave 2, S29a's last item).

A TEMPLATE is a plain-English description + a BPMN diagram an org copies into itself as a NEW workflow definition
(version 1) that it then owns. Two kinds:

  * PLATFORM templates — owned by the Hollisworks platform org (or, in a verify, an is_test fixture org): one Super
    Admin drafts a version, a DIFFERENT Super Admin publishes (or rejects) it; tenants read published versions only.
  * ORG templates — an org saves one of its own workflow versions as a template; visible only inside that org.

THE THIRD WRITER OF BPMN, THE ONE VALIDATOR. Every template version and every copy is checked by
``services.workflow_nl_generator._validate`` — the function the NL generator calls directly and the diagram editor
reaches through ``validate_workflow_bpmn``. It is always looked up on the module (``G._validate``,
``G.validate_workflow_bpmn``), never imported by name, so there is exactly one implementation to change (and a
verify can watch it being called). Nothing here re-implements a BPMN check; the template-only rules below are
POLICY about which verbs a platform template may name, layered on top of a document the shared validator accepted.

PARAMETERS. A template's BPMN may carry named placeholders ``{{name}}``. Each is declared (name, label, kind,
default …). Kinds: ``text`` (escaped into the XML), ``integer`` (min / max), ``choice`` (one of the declared keys)
and ``profile`` (one of the copying org's Profiles — substituted into ``assignedRoleProfileId``, checked by the
shared validator against that org's real Profiles; empty = the step is unassigned). A template version is
validated with every placeholder at its default (a profile placeholder empty); a copy is validated again with the
org's own values.

TIERS ARE COMPUTED, NEVER TYPED. A version stores its steps as the deriver reads them, with the registry tier,
access type, required permission and ``workflow_invocable`` of each verb and the EFFECTIVE tier the engine will use
(``workflow_engine.compute_effective_tier``). "Needs approval" = an invocable verb at effective tier 1 — exactly
the condition under which ``workflow_engine._drive`` suspends.

WAVE 2 IS READ-ONLY / DRAFT. A platform template may name a verb only if, in a run, it either only resolves (not
``workflow_invocable`` — no handler runs) or stops for a second person (invocable at effective tier 1). A
non-invocable Tier-1 write (money movement, an obligation, an ownership change) is refused too: a step named after
one would read as if the workflow did it.

THE COPY IS THE ORG'S. Copying INSERTs a brand-new definition + version 1 + derived steps + one provenance row
(``workflow_template_copies``). Nothing ever writes back to a template from a copy, and a new template version never
touches an existing copy: the org is told "a newer version exists" (with the step diff) and may start a new copy.
The copy has no trigger — nothing runs it until someone with ``configure_workflow_triggers`` adds one on the
existing Triggers screen (there is no other activation state on a definition; see docs/WFTEMPLATES1_DISCOVERY.md).

``org_id`` is always the caller's (``get_org_id``), never a body or path value. Every action is audited
(``services.audit.write_audit_log``). No model call, no network call.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from xml.sax.saxutils import escape as _xml_escape

import asyncpg

from services import workflow_nl_generator as G
from services.action_registry import REGISTRY
from services.audit import write_audit_log
from services.bpmn_layout import BpmnLayoutError, ensure_bpmn_di
from services.workflow_engine import SUSPEND_TIER, compute_effective_tier
from services.workflow_steps_deriver import derive_and_store_steps, derive_steps
from services import sod  # S30 — the ONE maker-checker rule

PLATFORM_ORG_ID = "bb347258-8f28-4f49-8cc9-e29ccad82884"

PERM_AUTHOR = "author_workflows"
PERM_VIEW_RUNS = "view_workflow_runs"
PERM_TRIGGERS = "configure_workflow_triggers"
PERM_REVIEW = "review_agent_proposals"
#: Browsing the catalog: any of the three workflow keys (a member holds none of them).
READ_PERMISSIONS = (PERM_AUTHOR, PERM_VIEW_RUNS, PERM_TRIGGERS)

T_TEMPLATES = "public.workflow_templates"
T_VERSIONS = "public.workflow_template_versions"
T_COPIES = "public.workflow_template_copies"

#: Category keys (the CHECK constraint's list) with their labels — published in every envelope (Rule 1).
CATEGORIES = (
    ("onboarding", "Onboarding"),
    ("reviews_compliance", "Reviews & compliance"),
    ("trading_rebalancing", "Trading & rebalancing"),
    ("cash_transfers", "Cash & transfers"),
    ("documents", "Documents"),
    ("alternatives", "Alternatives / capital calls"),
    ("tax", "Tax"),
    ("client_service", "Client service"),
)
CATEGORY_KEYS = frozenset(k for k, _ in CATEGORIES)
STATUSES = (("draft", "Draft — awaiting a second Super Admin"), ("published", "Published"),
            ("rejected", "Rejected"), ("retired", "Retired"))
SCOPES = (("platform", "Hollisworks"), ("org", "Your firm"))
PARAM_KINDS = ("text", "integer", "choice", "profile")
KEY_RX = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
PARAM_NAME_RX = re.compile(r"^[a-z][a-z0-9_]{1,40}$")
PLACEHOLDER_RX = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")
TEXT_MAX = 120

STEP_LABEL_PERSON = "A person does this step"
STEP_LABEL_SEND = "A person sends this (approval required)"
STEP_LABEL_APPROVAL = "Needs approval (Tier 1) — a second person approves before it runs"
STEP_LABEL_RESOLVE = "Reference step — the workflow records it; no action runs"
STEP_LABEL_AUTO = "Runs automatically"


# ═══ Errors ═════════════════════════════════════════════════════════════════
class TemplateError(Exception):
    """A request the rules refuse (422)."""

    def __init__(self, message: str, errors: list[str] | None = None):
        self.errors = errors or [message]
        super().__init__(message)


class TemplateNotFound(Exception):
    """Not visible to this org (404) — another org's template reads exactly like one that does not exist."""


class TemplateConflict(Exception):
    """The state forbids it (409)."""


class TemplatePermissionError(Exception):
    """The caller may not do this (403)."""


class MakerCheckerViolation(TemplatePermissionError):
    """The drafter tried to publish their own draft."""


# ═══ Small helpers ══════════════════════════════════════════════════════════
def _j(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


def _dumps(v) -> str:
    return json.dumps(v, default=str, sort_keys=True)


def _uuid(v) -> uuid.UUID:
    return v if isinstance(v, uuid.UUID) else uuid.UUID(str(v))


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def category_label(key: str) -> str:
    return dict(CATEGORIES).get(key, key)


def vocabularies(editable: list[str], platform_editable: list[str] | None = None) -> dict:
    return {
        "categories": [{"key": k, "label": v} for k, v in CATEGORIES],
        "statuses": [{"key": k, "label": v} for k, v in STATUSES],
        "scopes": [{"key": k, "label": v} for k, v in SCOPES],
        "parameter_kinds": list(PARAM_KINDS),
        "editable": list(editable),
        "inline_editable": [],
        "platform_editable": list(platform_editable or []),
    }


async def _audit(org_id, action: str, record_id, actor, new: dict | None = None) -> None:
    await write_audit_log(org_id=str(org_id), action=action, table_name="workflow_templates",
                          record_id=str(record_id), new=new, actor=str(actor) if actor else None)


async def _assert_super_admin(conn, actor_id):
    """The ONE shared helper (services.rbac.is_super_admin over services.authz.load_principal — user_roles)."""
    from services import rbac
    from services.authz import load_principal

    try:
        p = await load_principal(conn, actor_id)
    except (TypeError, ValueError):
        p = None
    if p is None or not rbac.is_super_admin(p):
        raise TemplatePermissionError("Super Admin required: platform workflow templates are Hollisworks staff only")
    return p


async def platform_org_allowed(conn, org_id) -> bool:
    return bool(await conn.fetchval("SELECT public.workflow_template_platform_org_allowed($1::uuid)", str(org_id)))


async def _archive_update(conn, row_id, fields: dict) -> dict:
    """System-axis edit of a template version: archive the outgoing state (a copy with archived_from_id), then
    change the live row (its id is kept — copies reference it)."""
    archived = await conn.fetchval(
        f"""INSERT INTO {T_VERSIONS}
            SELECT (jsonb_populate_record(NULL::{T_VERSIONS}, to_jsonb(t) || jsonb_build_object(
                'id', gen_random_uuid(), 'archived_from_id', t.id, 'system_to', now()))).*
            FROM {T_VERSIONS} t WHERE t.id = $1::uuid AND t.system_to IS NULL
            RETURNING id""", str(row_id))
    if archived is None:
        raise TemplateConflict("that template version is not live (not visible, or already archived)")
    cols = list(fields)
    sets = ", ".join(f"{c} = ${i + 2}" for i, c in enumerate(cols))
    row = await conn.fetchrow(f"UPDATE {T_VERSIONS} SET {sets}, system_from = now() WHERE id = $1::uuid "
                              "AND system_to IS NULL RETURNING *", str(row_id), *[fields[c] for c in cols])
    if row is None:
        raise TemplateConflict("the template version was not updated (not visible to this org, or not live)")
    return dict(row)


# ═══ Parameters ═════════════════════════════════════════════════════════════
def parse_parameters(spec) -> list[dict]:
    """Validate a parameter declaration list; returns it normalised. Raises TemplateError naming each problem."""
    if spec is None:
        return []
    if not isinstance(spec, list):
        raise TemplateError("parameters must be a list")
    out, errs, seen = [], [], set()
    for i, p in enumerate(spec):
        if not isinstance(p, dict):
            errs.append(f"parameter #{i + 1} is not an object")
            continue
        name, kind = p.get("name"), p.get("kind")
        if not isinstance(name, str) or not PARAM_NAME_RX.match(name):
            errs.append(f"parameter #{i + 1}: name {name!r} must be lower_snake_case (2–41 characters)")
            continue
        if name in seen:
            errs.append(f"parameter {name!r} is declared twice")
            continue
        seen.add(name)
        if kind not in PARAM_KINDS:
            errs.append(f"parameter {name!r}: kind must be one of {', '.join(PARAM_KINDS)}")
            continue
        label = p.get("label")
        if not isinstance(label, str) or not label.strip():
            errs.append(f"parameter {name!r} needs a label")
            continue
        q = {"name": name, "kind": kind, "label": label.strip(), "required": bool(p.get("required", True)),
             "help": (p.get("help") or "").strip() or None}
        if kind == "integer":
            lo, hi = p.get("min"), p.get("max")
            if not isinstance(lo, int) or not isinstance(hi, int) or isinstance(lo, bool) or lo > hi:
                errs.append(f"parameter {name!r}: an integer needs whole-number min ≤ max")
                continue
            q["min"], q["max"] = lo, hi
        if kind == "choice":
            ch = p.get("choices")
            if not isinstance(ch, list) or not ch or not all(
                    isinstance(c, dict) and isinstance(c.get("key"), str) and c["key"].strip()
                    and isinstance(c.get("label"), str) for c in ch):
                errs.append(f"parameter {name!r}: a choice needs a list of {{key, label}} choices")
                continue
            q["choices"] = [{"key": c["key"].strip(), "label": c["label"].strip()} for c in ch]
        if kind == "profile":
            q["default"] = None
        else:
            d = p.get("default")
            try:
                q["default"] = _coerce(q, d) if d is not None else None
            except TemplateError as exc:
                errs.append(f"parameter {name!r}: default — {exc}")
                continue
            if q["default"] is None:
                errs.append(f"parameter {name!r} needs a default (the template is validated with it)")
                continue
        out.append(q)
    if errs:
        raise TemplateError("the template's parameters are invalid", errs)
    return out


def _coerce(p: dict, value):
    """One parameter value, checked against its kind. Returns the value to substitute (a str) or None (empty)."""
    kind = p["kind"]
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if kind == "text":
        if not isinstance(value, str):
            raise TemplateError("must be text")
        v = value.strip()
        if len(v) > TEXT_MAX:
            raise TemplateError(f"is longer than {TEXT_MAX} characters")
        if "{{" in v or "}}" in v:
            raise TemplateError("may not contain {{ or }}")
        return v
    if kind == "integer":
        if isinstance(value, bool):
            raise TemplateError("must be a whole number")
        if isinstance(value, str) and re.fullmatch(r"-?\d+", value.strip()):
            value = int(value.strip())
        if not isinstance(value, int):
            raise TemplateError("must be a whole number")
        lo, hi = p.get("min"), p.get("max")
        if lo is not None and value < lo or hi is not None and value > hi:
            raise TemplateError(f"must be between {lo} and {hi}")
        return str(value)
    if kind == "choice":
        keys = [c["key"] for c in p.get("choices") or []]
        if value not in keys:
            raise TemplateError(f"must be one of {', '.join(keys)}")
        return value
    if kind == "profile":
        try:
            return str(uuid.UUID(str(value)))
        except (ValueError, TypeError) as exc:
            raise TemplateError("must be one of your Profiles (a Profile id)") from exc
    raise TemplateError(f"unknown kind {kind!r}")


def placeholders(xml: str) -> list[str]:
    return sorted(set(PLACEHOLDER_RX.findall(xml or "")))


def substitute(xml: str, params: list[dict], values: dict | None, *, for_validation: bool = False) -> tuple[str, dict]:
    """Fill every ``{{name}}``. ``for_validation``: each at its default, a profile placeholder empty. Otherwise the
    caller's ``values`` (a missing optional value falls back to the default; a missing required one is an error).
    Returns ``(xml, the values used)``. Every value is XML-escaped (attribute-safe)."""
    declared = {p["name"]: p for p in params}
    used = placeholders(xml)
    errs = [f"placeholder {{{{{n}}}}} is not a declared parameter" for n in used if n not in declared]
    errs += [f"parameter {n!r} is declared but never used in the diagram" for n in declared if n not in used]
    values = values or {}
    if not isinstance(values, dict):
        raise TemplateError("parameter values must be an object")
    errs += [f"{n!r} is not a parameter of this template" for n in values if n not in declared]
    final: dict = {}
    for n, p in declared.items():
        if for_validation:
            final[n] = None if p["kind"] == "profile" else p.get("default")
            continue
        try:
            v = _coerce(p, values.get(n))
        except TemplateError as exc:
            errs.append(f"{p['label']} ({n}) {exc}")
            continue
        if v is None and p["kind"] != "profile":
            v = p.get("default")
        if v is None and p.get("required") and p["kind"] != "profile":
            errs.append(f"{p['label']} ({n}) is required")
        final[n] = v
    if errs:
        raise TemplateError("the parameters do not fit this template", errs)

    def rep(m):
        v = final.get(m.group(1))
        return _xml_escape("" if v is None else str(v), {'"': "&quot;", "'": "&apos;"})

    return PLACEHOLDER_RX.sub(rep, xml), final


# ═══ The shared validator + computed tiers ══════════════════════════════════
def _registry_keys() -> set[str]:
    return {a.key for a in REGISTRY.all()}


def tier_steps(xml: str) -> list[dict]:
    """The steps the deriver reads from ``xml`` (already validated), each with its verb's registry facts and the
    EFFECTIVE tier the engine will use."""
    out = []
    for s in derive_steps(xml):
        action = REGISTRY.get(s["action_registry_key"]) if s["action_registry_key"] else None
        eff = compute_effective_tier(s, action) if s["step_type"] == "service" else s["autonomy_tier"]
        invocable = bool(getattr(action, "workflow_invocable", False))
        needs = s["step_type"] == "service" and invocable and eff == SUSPEND_TIER
        if s["step_type"] == "user":
            label = STEP_LABEL_PERSON
        elif s["step_type"] == "send":
            label = STEP_LABEL_SEND
        elif s["step_type"] == "service":
            label = STEP_LABEL_APPROVAL if needs else (f"{STEP_LABEL_AUTO} (Tier {eff})" if invocable
                                                       else f"{STEP_LABEL_RESOLVE} (Tier {eff})")
        else:
            label = f"Tier {eff}"
        out.append({
            "step_key": s["step_key"], "step_type": s["step_type"], "display_name": s["display_name"],
            "action_registry_key": s["action_registry_key"], "diagram_tier": s["autonomy_tier"],
            "registry_tier": getattr(action, "tier", None), "effective_tier": eff,
            "access_type": getattr(action, "access_type", None),
            "required_permission": getattr(action, "required_permission", None),
            "invocable": invocable, "needs_approval": needs, "label": label,
            "has_profile": bool(s["assigned_role_profile_id"]),
        })
    return out


def wave2_refusals(steps: list[dict]) -> list[str]:
    """Platform-template POLICY (on top of a validated document): every service step only resolves, or stops for a
    second person; no step names a Tier-1 money / obligation / ownership verb."""
    errs = []
    for s in steps:
        if s["step_type"] != "service":
            continue
        k = s["action_registry_key"]
        if s["invocable"] and s["effective_tier"] != SUSPEND_TIER:
            errs.append(f"service task '{s['step_key']}' would run '{k}' unattended (Tier {s['effective_tier']}) — a "
                        "Wave-2 template only reads, drafts, or stops for approval")
        if not s["invocable"] and s["access_type"] == "write" and s["registry_tier"] == SUSPEND_TIER:
            errs.append(f"service task '{s['step_key']}' names '{k}', a Tier-1 write (money, an obligation or "
                        "ownership) — make it a user task a person does")
    return errs


def validate_template(xml: str, params: list[dict], *, profile_ids: set[str] | None = None,
                      platform: bool = False) -> list[dict]:
    """Validate a template version (every placeholder at its default) through THE shared validator; return its
    tiered steps. Raises TemplateError with the validator's own findings."""
    if not isinstance(xml, str) or not xml.strip():
        raise TemplateError("bpmn_xml is required")
    sub, _ = substitute(xml, params, None, for_validation=True)
    errors = G._validate(sub, _registry_keys(), set(profile_ids or ()))
    if errors:
        raise TemplateError("the template's BPMN failed validation", errors)
    steps = tier_steps(sub)
    if platform:
        errs = wave2_refusals(steps)
        if errs:
            raise TemplateError("the template is not Wave-2 safe", errs)
    return steps


def prerequisites(steps: list[dict], params: list[dict]) -> list[dict]:
    """What an org must have to use the template well. Stored with the version; evaluated per caller on read."""
    out = [{"kind": "permission", "key": PERM_AUTHOR, "why": "to copy this template into your firm"},
           {"kind": "permission", "key": PERM_TRIGGERS,
            "why": "to schedule the copy or start it on an event (nothing runs it until then)"}]
    seen = {PERM_AUTHOR, PERM_TRIGGERS}
    for s in steps:
        p = s.get("required_permission")
        if s["step_type"] == "service" and s["invocable"] and p and p not in seen:
            seen.add(p)
            out.append({"kind": "permission", "key": p,
                        "why": f"the member who starts a run (or the trigger's creator) must hold it for "
                               f"'{s['display_name'] or s['step_key']}'"})
    if any(s["needs_approval"] for s in steps):
        out.append({"kind": "reviewer", "key": PERM_REVIEW,
                    "why": "a second person in your firm approves each Tier-1 step before it runs"})
    if any(p["kind"] == "profile" for p in params):
        out.append({"kind": "feature", "key": "profiles",
                    "why": "the reviewer steps are assigned to one of your Profiles"})
    return out


async def _review_holders(conn, org_id) -> int | None:
    try:
        return int(await conn.fetchval(
            """SELECT count(DISTINCT ur.user_id) FROM user_roles ur JOIN roles r ON r.id = ur.role_id
               JOIN role_permissions rp ON rp.role_id = r.id JOIN permissions p ON p.id = rp.permission_id
               JOIN users u ON u.id = ur.user_id
               WHERE r.org_id = $1::uuid AND p.name = $2 AND u.is_active AND u.status = 'active'""",
            str(org_id), PERM_REVIEW))
    except asyncpg.PostgresError:
        return None


async def evaluate_prerequisites(conn, org_id, principal, prereqs: list[dict]) -> list[dict]:
    """Each prerequisite with ``met`` (True / False / None = could not tell) and, when missing, what is missing."""
    out = []
    holders = None
    profiles = None
    for p in prereqs or []:
        q = dict(p)
        if p["kind"] == "permission":
            q["met"] = bool(principal is not None and principal.has(p["key"]))
            q["missing"] = None if q["met"] else f"you do not hold {p['key']}"
        elif p["kind"] == "reviewer":
            if holders is None:
                holders = await _review_holders(conn, org_id)
            q["met"] = None if holders is None else holders > 0
            q["missing"] = None if q["met"] is not False else f"no one in your firm holds {p['key']}"
        elif p["kind"] == "feature" and p["key"] == "profiles":
            if profiles is None:
                profiles = int(await conn.fetchval("SELECT count(*) FROM profiles WHERE org_id = $1::uuid",
                                                   str(org_id)))
            q["met"] = profiles > 0
            q["missing"] = None if q["met"] else "your firm has no Profiles yet (the steps will be unassigned)"
        else:
            q["met"], q["missing"] = None, None
        out.append(q)
    return out


def drawable(xml: str, params: list[dict]) -> str:
    """The diagram a viewer draws: placeholders at their defaults (a profile empty), with a generated layout."""
    try:
        sub, _ = substitute(xml, params, None, for_validation=True)
    except TemplateError:
        sub = xml
    try:
        return ensure_bpmn_di(sub)
    except BpmnLayoutError:
        return sub


def steps_diff(before: list[dict], after: list[dict]) -> dict:
    """What changed between two template versions' steps (by step key)."""
    b = {s["step_key"]: s for s in before or []}
    a = {s["step_key"]: s for s in after or []}
    fields = ("display_name", "step_type", "action_registry_key", "effective_tier", "needs_approval")
    changed = []
    for k in sorted(set(a) & set(b)):
        for f in fields:
            if a[k].get(f) != b[k].get(f):
                changed.append({"step_key": k, "field": f, "before": b[k].get(f), "after": a[k].get(f)})
    return {"added": [{"step_key": k, "display_name": a[k].get("display_name")} for k in a if k not in b],
            "removed": [{"step_key": k, "display_name": b[k].get("display_name")} for k in b if k not in a],
            "changed": changed}


# ═══ Reads ══════════════════════════════════════════════════════════════════
def version_out(r, *, with_xml: bool = False) -> dict:
    r = dict(r)
    params = _j(r["parameters"]) or []
    out = {
        "id": str(r["id"]), "template_id": str(r["template_id"]), "version_no": r["version_no"],
        "status": r["status"], "scope": r["scope"], "title": r["title"], "category": r["category"],
        "category_label": category_label(r["category"]), "description": r["description"],
        "steps": _j(r["steps"]) or [], "parameters": params, "prerequisites": _j(r["prerequisites"]) or [],
        "bpmn_sha256": r["bpmn_sha256"], "drafted_at": r["drafted_at"], "published_at": r["published_at"],
        "retired_at": r["retired_at"],
    }
    if with_xml:
        out["bpmn_xml"] = r["bpmn_xml"]
        out["drawable_xml"] = drawable(r["bpmn_xml"], params)
    return out


# "The catalog this org can read", stated in SQL — not left to RLS alone: a Super Admin's requests run under the
# Super Admin GUC, which RLS lets read every firm's org templates and every fixture owner's platform templates. The
# catalog is explicitly the platform templates THIS org may see (workflow_template_visible — the same rule the
# policy uses) plus this org's own. $1 = the caller's org.
_LATEST_PUBLISHED = f"""
    SELECT DISTINCT ON (v.template_id) v.*, t.template_key
      FROM {T_VERSIONS} v JOIN {T_TEMPLATES} t ON t.id = v.template_id AND t.system_to IS NULL
     WHERE v.system_to IS NULL AND v.status = 'published'
       AND (v.owner_org_id = $1::uuid
            OR (v.scope = 'platform' AND public.workflow_template_visible(v.owner_org_id)))
     ORDER BY v.template_id, v.version_no DESC"""


async def catalog(pool, *, org_id, caller, category: str | None = None, q: str | None = None) -> dict:
    """The catalog this org can read: published platform templates (RLS: visible owners only) and the org's own
    templates, each at its latest PUBLISHED version. Filter by category; search title / description / key."""
    if category and category not in CATEGORY_KEYS:
        raise TemplateError(f"unknown category {category!r}")
    async with pool.acquire() as conn:
        rows = [dict(r) for r in await conn.fetch(_LATEST_PUBLISHED, str(org_id))]
        copies = {str(r["template_id"]): int(r["n"]) for r in await conn.fetch(
            f"SELECT template_id, count(*) AS n FROM {T_COPIES} WHERE org_id = $1::uuid GROUP BY template_id",
            str(org_id))}
    needle = (q or "").strip().lower()
    items = []
    for r in rows:
        if category and r["category"] != category:
            continue
        if needle and needle not in " ".join([r["title"], r["description"], r["template_key"]]).lower():
            continue
        steps = _j(r["steps"]) or []
        items.append({
            "template_id": str(r["template_id"]), "key": r["template_key"], "scope": r["scope"],
            "is_own": str(r["owner_org_id"]) == str(org_id), "version_id": str(r["id"]),
            "version_no": r["version_no"], "title": r["title"], "category": r["category"],
            "category_label": category_label(r["category"]), "description": r["description"],
            "step_count": len(steps), "person_steps": sum(1 for s in steps if s["step_type"] in ("user", "send")),
            "approval_steps": sum(1 for s in steps if s.get("needs_approval")),
            "parameter_count": len(_j(r["parameters"]) or []), "my_copies": copies.get(str(r["template_id"]), 0),
        })
    items.sort(key=lambda i: (dict((k, n) for n, (k, _) in enumerate(CATEGORIES)).get(i["category"], 99),
                              i["title"].lower()))
    return {"templates": items}


async def _template_row(conn, template_id) -> dict:
    t = await conn.fetchrow(f"SELECT * FROM {T_TEMPLATES} WHERE id = $1::uuid AND system_to IS NULL", str(template_id))
    if t is None:
        raise TemplateNotFound("template not found")
    return dict(t)


async def _readable_versions(conn, template_id) -> list[dict]:
    return [dict(r) for r in await conn.fetch(
        f"SELECT * FROM {T_VERSIONS} WHERE template_id = $1::uuid AND system_to IS NULL ORDER BY version_no DESC",
        str(template_id))]


async def org_copies(conn, org_id, template_id=None) -> list[dict]:
    """This org's copies, each with whether a NEWER published version of its template exists (and the step diff).
    Nothing is overwritten — the org may start a new copy."""
    rows = [dict(r) for r in await conn.fetch(
        f"""SELECT c.*, v.title, v.steps AS copied_steps, d.name AS definition_name
              FROM {T_COPIES} c
              JOIN {T_VERSIONS} v ON v.id = c.template_version_id
              LEFT JOIN workflow_definitions d ON d.id = c.workflow_definition_id AND d.org_id = c.org_id
             WHERE c.org_id = $1::uuid AND ($2::uuid IS NULL OR c.template_id = $2::uuid)
             ORDER BY c.created_at DESC""", str(org_id), str(template_id) if template_id else None)]
    latest = {str(r["template_id"]): dict(r) for r in await conn.fetch(_LATEST_PUBLISHED, str(org_id))}
    out = []
    for c in rows:
        lv = latest.get(str(c["template_id"]))
        newer = lv is not None and lv["version_no"] > c["template_version_no"]
        out.append({
            "id": str(c["id"]), "template_id": str(c["template_id"]), "title": c["title"],
            "template_version_id": str(c["template_version_id"]), "template_version_no": c["template_version_no"],
            "workflow_definition_id": str(c["workflow_definition_id"]),
            "workflow_version_id": str(c["workflow_version_id"]),
            "definition_name": c["definition_name"], "definition_exists": c["definition_name"] is not None,
            "parameter_values": _j(c["parameter_values"]) or {}, "copied_at": c["created_at"],
            "newer_version": ({"version_id": str(lv["id"]), "version_no": lv["version_no"],
                               "diff": steps_diff(_j(c["copied_steps"]) or [], _j(lv["steps"]) or [])}
                              if newer else None),
        })
    return out


async def detail(pool, *, org_id, caller, template_id, version_id=None) -> dict:
    async with pool.acquire() as conn:
        t = await _template_row(conn, template_id)
        versions = await _readable_versions(conn, template_id)
        own = str(t["owner_org_id"]) == str(org_id)
        if version_id:
            chosen = next((v for v in versions if str(v["id"]) == str(version_id)), None)
            if chosen is None:
                raise TemplateNotFound("template version not found")
        else:
            # the newest published version; else (an owner's draft, or a tenant whose versions are all retired) the
            # newest version this org can read — shown, never copied (can_use_version is False)
            chosen = next((v for v in versions if v["status"] == "published"), None) or (versions[0] if versions
                                                                                         else None)
        if chosen is None:
            raise TemplateNotFound("template not found")
        prereqs = await evaluate_prerequisites(conn, org_id, caller, _j(chosen["prerequisites"]) or [])
        profiles = [{"id": str(r["id"]), "name": r["name"]} for r in await conn.fetch(
            "SELECT id, name FROM profiles WHERE org_id = $1::uuid ORDER BY name", str(org_id))]
        copies = await org_copies(conn, org_id, template_id)
    latest_pub = next((v for v in versions if v["status"] == "published"), None)
    return {
        "template": {"id": str(t["id"]), "key": t["template_key"], "scope": t["scope"], "is_own": own},
        "version": version_out(chosen, with_xml=True),
        "latest_published_version_no": latest_pub["version_no"] if latest_pub else None,
        "versions": [{"id": str(v["id"]), "version_no": v["version_no"], "status": v["status"],
                      "published_at": v["published_at"], "retired_at": v["retired_at"]} for v in versions],
        "prerequisites": prereqs,
        "missing_prerequisites": [p["missing"] for p in prereqs if p.get("met") is False and p.get("missing")],
        "profiles": profiles,
        "my_copies": copies,
        "can_use_version": chosen["status"] == "published",
    }


# ═══ Use a template ═════════════════════════════════════════════════════════
async def use_template(pool, *, org_id, actor_id, template_id, version_id=None, values: dict | None = None,
                       name: str | None = None) -> dict:
    """Copy a PUBLISHED template version into ``org_id`` as a NEW draft workflow definition + version 1.

    Parameters substituted, the result re-validated by the shared validator against THIS org's Profiles, laid out,
    then ONE transaction: definition, version 1 (is_current), derived steps, the provenance row. No trigger is
    created — the copy is a draft until someone adds one."""
    async with pool.acquire() as conn:
        t = await _template_row(conn, template_id)
        if version_id:
            v = await conn.fetchrow(f"SELECT * FROM {T_VERSIONS} WHERE id = $1::uuid AND template_id = $2::uuid AND "
                                    "system_to IS NULL", str(version_id), str(template_id))
        else:
            v = await conn.fetchrow(f"SELECT * FROM {T_VERSIONS} WHERE template_id = $1::uuid AND system_to IS NULL "
                                    "AND status = 'published' ORDER BY version_no DESC LIMIT 1", str(template_id))
        if v is None:
            raise TemplateNotFound("template version not found")
        v = dict(v)
        if v["status"] != "published":
            raise TemplateConflict(f"version {v['version_no']} is {v['status']} — only a published version is copied")
        params = _j(v["parameters"]) or []
        xml, used = substitute(v["bpmn_xml"], params, values)
        errors = await G.validate_workflow_bpmn(conn, org_id, xml)
    if errors:
        raise TemplateError("the copy failed validation", errors)
    xml = ensure_bpmn_di(xml)
    wf_name = (name or "").strip() or v["title"]
    if len(wf_name) > 200:
        raise TemplateError("the workflow name is longer than 200 characters")
    summary = f"Copied from template '{t['template_key']}' version {v['version_no']}"
    async with pool.acquire() as conn:
        async with conn.transaction():
            definition_id = await conn.fetchval(
                "INSERT INTO workflow_definitions (org_id, name, description, created_by) VALUES ($1, $2, $3, $4) "
                "RETURNING id", _uuid(org_id), wf_name, v["description"], _uuid(actor_id))
            wf_version_id = await conn.fetchval(
                "INSERT INTO workflow_versions (workflow_definition_id, org_id, version_number, bpmn_xml, "
                "change_summary, is_current, created_by) VALUES ($1, $2, 1, $3, $4, true, $5) RETURNING id",
                definition_id, _uuid(org_id), xml, summary, _uuid(actor_id))
            steps = await derive_and_store_steps(conn, wf_version_id, _uuid(org_id), xml)
            copy_id = await conn.fetchval(
                f"""INSERT INTO {T_COPIES} (org_id, template_id, template_version_id, template_owner_org_id,
                        template_version_no, workflow_definition_id, workflow_version_id, parameter_values, copied_by)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9) RETURNING id""",
                _uuid(org_id), v["template_id"], v["id"], v["owner_org_id"], v["version_no"], definition_id,
                wf_version_id, _dumps(used), _uuid(actor_id))
    await _audit(org_id, "workflow_template_copied", copy_id, actor_id,
                 {"template_id": str(v["template_id"]), "template_version_id": str(v["id"]),
                  "version_no": v["version_no"], "workflow_definition_id": str(definition_id)})
    return {"copy_id": str(copy_id), "workflow_definition_id": str(definition_id),
            "workflow_version_id": str(wf_version_id), "version_number": 1, "name": wf_name,
            "template_version_no": v["version_no"], "step_count": len(steps), "parameter_values": used,
            "triggers": 0}


# ═══ Writes: templates and versions ═════════════════════════════════════════
async def _get_or_create_template(conn, *, owner_org, scope: str, key: str, actor_id) -> dict:
    t = await conn.fetchrow(f"SELECT * FROM {T_TEMPLATES} WHERE owner_org_id = $1::uuid AND template_key = $2 AND "
                            "system_to IS NULL", str(owner_org), key)
    if t is not None:
        if t["scope"] != scope:
            raise TemplateConflict(f"the key {key!r} is already a {t['scope']} template of this org")
        return dict(t)
    return dict(await conn.fetchrow(
        f"INSERT INTO {T_TEMPLATES} (owner_org_id, scope, template_key, created_by) VALUES ($1, $2, $3, $4) "
        "RETURNING *", _uuid(owner_org), scope, key, _uuid(actor_id)))


def _content_checks(key, title, category, description) -> None:
    errs = []
    if not isinstance(key, str) or not KEY_RX.match(key):
        errs.append("key must be lower_snake_case, 3–64 characters, starting with a letter")
    if not isinstance(title, str) or not title.strip() or len(title.strip()) > 200:
        errs.append("title is required (at most 200 characters)")
    if category not in CATEGORY_KEYS:
        errs.append(f"category must be one of {', '.join(sorted(CATEGORY_KEYS))}")
    if not isinstance(description, str) or not description.strip() or len(description.strip()) > 8000:
        errs.append("a plain-English description is required (at most 8000 characters)")
    if errs:
        raise TemplateError("the template is incomplete", errs)


async def _insert_version(conn, *, template: dict, status: str, title, category, description, xml, steps, params,
                          actor_id, source_definition_id=None, source_version_id=None) -> dict:
    n = await conn.fetchval(f"SELECT COALESCE(max(version_no), 0) FROM {T_VERSIONS} WHERE template_id = $1::uuid AND "
                            "system_to IS NULL", str(template["id"]))
    prereqs = prerequisites(steps, params)
    try:
        row = await conn.fetchrow(
            f"""INSERT INTO {T_VERSIONS} (template_id, owner_org_id, scope, version_no, status, title, category,
                    description, bpmn_xml, bpmn_sha256, steps, parameters, prerequisites, source_definition_id,
                    source_version_id, drafted_by, published_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::jsonb, $12::jsonb, $13::jsonb, $14, $15, $16,
                        CASE WHEN $5 = 'published' THEN now() END)
                RETURNING *""",
            template["id"], template["owner_org_id"], template["scope"], n + 1, status, title.strip(), category,
            description.strip(), xml, sha256(xml), _dumps(steps), _dumps(params), _dumps(prereqs),
            source_definition_id, source_version_id, _uuid(actor_id))
    except asyncpg.UniqueViolationError as exc:
        raise TemplateConflict("a draft of this template is already awaiting a second Super Admin — decide it "
                               "first") from exc
    return dict(row)


async def draft_platform_template(pool, *, org_id, actor_id, key, title, category, description, bpmn_xml,
                                  parameters=None) -> dict:
    """A Super Admin drafts a PLATFORM template version (in the caller's org, which must be the Hollisworks platform
    org or an is_test org). A different Super Admin publishes it."""
    _content_checks(key, title, category, description)
    params = parse_parameters(parameters)
    # A platform template cannot name an org's Profile: profile ids are checked against the EMPTY set.
    steps = validate_template(bpmn_xml, params, profile_ids=set(), platform=True)
    async with pool.acquire() as conn:
        await _assert_super_admin(conn, actor_id)
        if not await platform_org_allowed(conn, org_id):
            raise TemplatePermissionError("platform templates are drafted in the Hollisworks platform org")
        async with conn.transaction():
            t = await _get_or_create_template(conn, owner_org=org_id, scope="platform", key=key, actor_id=actor_id)
            row = await _insert_version(conn, template=t, status="draft", title=title, category=category,
                                        description=description, xml=bpmn_xml, steps=steps, params=params,
                                        actor_id=actor_id)
    await _audit(org_id, "workflow_template_drafted", row["id"], actor_id,
                 {"template_id": str(t["id"]), "key": key, "version_no": row["version_no"]})
    return version_out(row)


async def _platform_version(conn, org_id, version_id, *, lock=False) -> dict:
    v = await conn.fetchrow(f"SELECT * FROM {T_VERSIONS} WHERE id = $1::uuid AND owner_org_id = $2::uuid AND "
                            "scope = 'platform' AND system_to IS NULL" + (" FOR UPDATE" if lock else ""),
                            str(version_id), str(org_id))
    if v is None:
        raise TemplateNotFound("platform template version not found")
    return dict(v)


async def decide_platform_version(pool, *, org_id, actor_id, version_id, outcome: str, note: str | None = None) -> dict:
    """Publish or reject a platform draft. Maker-checker: the drafter never decides their own draft (the service
    refuses, and the database CHECK would refuse too)."""
    if outcome not in ("approve", "reject"):
        raise TemplateError("outcome is approve or reject")
    if outcome == "reject" and not (note or "").strip():
        raise TemplateError("rejecting needs a note for the drafter")
    async with pool.acquire() as conn:
        await _assert_super_admin(conn, actor_id)
        async with conn.transaction():
            v = await _platform_version(conn, org_id, version_id, lock=True)
            if v["status"] != "draft":
                raise TemplateConflict(f"version {v['version_no']} is {v['status']} — only a draft is decided")
            await sod.enforce(conn, org_id=org_id, item_type=sod.WORKFLOW_TEMPLATE_VERSION, checker_id=actor_id,
                           maker_ids=[v["drafted_by"]], item_id=v["id"], site="workflow_templates.decide_platform_version",
                           record=sod.RECORD_ALL, refuse=lambda d: MakerCheckerViolation("maker-checker: you drafted this version — a different Super Admin "
                                                                "publishes it"))
            now = datetime.now(timezone.utc)
            fields = ({"status": "published", "checker_id": _uuid(actor_id), "published_at": now,
                       "decision_note": (note or "").strip() or None} if outcome == "approve" else
                      {"status": "rejected", "checker_id": _uuid(actor_id), "rejected_at": now,
                       "decision_note": note.strip()})
            row = await _archive_update(conn, v["id"], fields)
    await _audit(org_id, "workflow_template_published" if outcome == "approve" else "workflow_template_rejected",
                 v["id"], actor_id, {"template_id": str(v["template_id"]), "version_no": v["version_no"]})
    return version_out(row)


async def retire_version(pool, *, org_id, actor_id, version_id, reason: str, platform: bool) -> dict:
    """Retire a published version. Existing copies keep working — they are the org's own definitions and never
    read the template again. A retired version is no longer copied."""
    if not (reason or "").strip():
        raise TemplateError("retiring needs a reason")
    async with pool.acquire() as conn:
        if platform:
            await _assert_super_admin(conn, actor_id)
        async with conn.transaction():
            if platform:
                v = await _platform_version(conn, org_id, version_id, lock=True)
            else:
                v = await conn.fetchrow(f"SELECT * FROM {T_VERSIONS} WHERE id = $1::uuid AND owner_org_id = $2::uuid "
                                        "AND scope = 'org' AND system_to IS NULL FOR UPDATE", str(version_id),
                                        str(org_id))
                if v is None:
                    raise TemplateNotFound("template version not found")
                v = dict(v)
            if v["status"] != "published":
                raise TemplateConflict(f"only a published version is retired (this one is {v['status']})")
            row = await _archive_update(conn, v["id"], {"status": "retired", "retired_by": _uuid(actor_id),
                                                        "retired_at": datetime.now(timezone.utc),
                                                        "retire_reason": reason.strip()})
    await _audit(org_id, "workflow_template_retired", v["id"], actor_id,
                 {"template_id": str(v["template_id"]), "version_no": v["version_no"], "reason": reason.strip()})
    return version_out(row)


async def save_org_template(pool, *, org_id, actor_id, workflow_definition_id, key, category, title=None,
                            description=None, parameters=None) -> dict:
    """Save the CURRENT version of one of this org's workflows as an ORG template version (published inside the
    org; never visible to another org). Same shared validation, against this org's own Profiles."""
    async with pool.acquire() as conn:
        d = await conn.fetchrow("SELECT id, name, description FROM workflow_definitions WHERE id = $1::uuid AND "
                                "org_id = $2::uuid", str(workflow_definition_id), str(org_id))
        if d is None:
            raise TemplateNotFound("workflow not found")
        wv = await conn.fetchrow("SELECT id, bpmn_xml, version_number FROM workflow_versions WHERE "
                                 "workflow_definition_id = $1 AND org_id = $2::uuid AND is_current = true",
                                 d["id"], str(org_id))
        if wv is None:
            raise TemplateConflict("the workflow has no current version")
        profile_ids = {str(r["id"]) for r in await conn.fetch("SELECT id FROM profiles WHERE org_id = $1::uuid",
                                                              str(org_id))}
    title = title if (title or "").strip() else d["name"]
    description = description if (description or "").strip() else (d["description"] or "")
    _content_checks(key, title, category, description)
    params = parse_parameters(parameters)
    steps = validate_template(wv["bpmn_xml"], params, profile_ids=profile_ids, platform=False)
    async with pool.acquire() as conn:
        async with conn.transaction():
            t = await _get_or_create_template(conn, owner_org=org_id, scope="org", key=key, actor_id=actor_id)
            row = await _insert_version(conn, template=t, status="published", title=title, category=category,
                                        description=description, xml=wv["bpmn_xml"], steps=steps, params=params,
                                        actor_id=actor_id, source_definition_id=d["id"], source_version_id=wv["id"])
    await _audit(org_id, "workflow_template_saved", row["id"], actor_id,
                 {"template_id": str(t["id"]), "key": key, "version_no": row["version_no"],
                  "workflow_definition_id": str(d["id"]), "workflow_version_number": wv["version_number"]})
    return version_out(row)


# ═══ Platform screen ════════════════════════════════════════════════════════
async def platform_overview(pool, *, org_id, actor_id) -> dict:
    """Every platform template this (platform) org owns, every live version with its status, and usage COUNTS
    (copies made, distinct orgs) from ``workflow_template_usage`` — never an org id, a definition or a person."""
    async with pool.acquire() as conn:
        await _assert_super_admin(conn, actor_id)
        allowed = await platform_org_allowed(conn, org_id)
        rows = [dict(r) for r in await conn.fetch(
            f"""SELECT v.*, t.template_key FROM {T_VERSIONS} v JOIN {T_TEMPLATES} t ON t.id = v.template_id
                 WHERE v.owner_org_id = $1::uuid AND v.scope = 'platform' AND v.system_to IS NULL
                 ORDER BY t.template_key, v.version_no DESC""", str(org_id))]
        usage = {str(r["template_version_id"]): (int(r["copies"]), int(r["orgs"])) for r in await conn.fetch(
            "SELECT * FROM public.workflow_template_usage($1::uuid)", str(org_id))}
    by: dict[str, dict] = {}
    for r in rows:
        t = by.setdefault(str(r["template_id"]), {"template_id": str(r["template_id"]), "key": r["template_key"],
                                                  "versions": [], "copies": 0})
        c, o = usage.get(str(r["id"]), (0, 0))
        t["copies"] += c
        out = version_out(r)
        out.update({"drafted_by": str(r["drafted_by"]), "checker_id": str(r["checker_id"]) if r["checker_id"] else None,
                    "decision_note": r["decision_note"], "retire_reason": r["retire_reason"], "copies": c, "orgs": o,
                    "can_decide": r["status"] == "draft" and sod.may_check_display(actor_id, [r["drafted_by"]]),
                    "can_retire": r["status"] == "published"})
        t["versions"].append(out)
    return {"platform_org_allowed": allowed, "templates": list(by.values())}


# ═══ Shipped template files (docs/workflow_templates/<key>.bpmn + <key>.md) ═══
_META_RX = re.compile(r"```json template\s*\n(.*?)\n```", re.DOTALL)


def read_template_file(directory, key: str) -> dict:
    """One shipped template: ``<key>.md`` carries a ```json template``` block (key, title, category, parameters)
    followed by the plain-English description (the NL source); ``<key>.bpmn`` the diagram. Pure — no database."""
    import pathlib

    d = pathlib.Path(directory)
    md = (d / f"{key}.md").read_text(encoding="utf-8")
    xml = (d / f"{key}.bpmn").read_text(encoding="utf-8")
    m = _META_RX.search(md)
    if m is None:
        raise TemplateError(f"{key}.md has no ```json template``` block")
    meta = json.loads(m.group(1))
    description = md[m.end():].strip()
    if meta.get("key") != key:
        raise TemplateError(f"{key}.md declares key {meta.get('key')!r}")
    return {"key": key, "title": meta.get("title"), "category": meta.get("category"),
            "parameters": meta.get("parameters") or [], "description": description, "bpmn_xml": xml}


def shipped_keys(directory) -> list[str]:
    import pathlib

    d = pathlib.Path(directory)
    return sorted(p.stem for p in d.glob("*.bpmn") if (d / f"{p.stem}.md").exists())
