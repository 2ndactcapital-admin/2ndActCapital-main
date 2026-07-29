"""Generic BPMN-XML → workflow_steps derivation (Workflow Manager — Phase 2).

Phase 1 proved a hand-written BPMN fixture executes, but its ``workflow_steps``
rows were inserted **by hand** in the verify script — there was no code that
derives step metadata from arbitrary BPMN XML.  This module is that missing
prerequisite: given a workflow version's ``bpmn_xml`` it produces exactly one
``workflow_steps`` row per actionable BPMN element.

Design:
  * Executability is validated by reusing Phase 1's parser (``parse_bpmn``) — no
    duplicate SpiffWorkflow wiring here.
  * Step *metadata* is read straight from the raw XML with lxml, because the
    SpiffWorkflow spec object deliberately discards vendor extension elements
    (the base ServiceTask converter "carries no extra attributes").
  * ``action_registry_key`` / ``assigned_role_profile_id`` (and an optional
    explicit ``autonomyTier`` override) live in a standard BPMN
    ``<bpmn:extensionElements>`` block under our own namespace — the mechanism
    both bpmn-js and SpiffWorkflow support for custom metadata.

Autonomy-tier default table (applied unless the XML's own governance extension
sets ``autonomyTier`` explicitly).  The load-bearing nuance: a Service Task that
wraps a WRITE-capable action is NEVER Tier 3 by default — silently defaulting
unreviewed autonomous writes to "fully autonomous" would be backwards.

  User Task .................................. Tier 1 (always)
  Service Task, action is READ-only .......... Tier 3 (safe to run autonomously)
  Service Task, action is WRITE-capable ...... Tier 2 (confirm-and-log)
  Service Task, action unknown/unresolved .... Tier 2 (safe default, never 3)
  Business Rule Task (DMN) ................... Tier 3 (deterministic evaluation)
  Send Task .................................. Tier 1 (never send without approval)
  Gateways / Start / End events .............. no workflow_steps row at all
"""
from __future__ import annotations

from uuid import UUID

from lxml import etree

from services.action_registry import REGISTRY
from services.workflow_engine import parse_bpmn

# Standard BPMN 2.0 model namespace + our custom governance-extension namespace.
BPMN_NS = "http://www.omg.org/spec/BPMN/20100524/MODEL"
EXT_NS = "http://2ndactcapital.com/bpmn/ext"

# BPMN element localname -> workflow_steps.step_type.  Only these four get a row;
# gateways, events and sequence flows are pure control flow and are skipped.
ACTIONABLE_TYPES = {
    "serviceTask": "service",
    "userTask": "user",
    "sendTask": "send",
    "businessRuleTask": "business_rule",
}

_EXT_TAG = "{%s}extensionElements" % BPMN_NS
_GOV_TAG = "{%s}governance" % EXT_NS


class DeriverError(Exception):
    """Raised when the BPMN XML is structurally unusable for step derivation."""


def _governance(el) -> tuple[str | None, str | None, int | None]:
    """Extract (action_registry_key, assigned_role_profile_id, explicit_tier).

    Reads the optional ``<bpmn:extensionElements><twoa:governance .../>`` child.
    Any attribute may be absent; a missing block yields ``(None, None, None)``.
    """
    ext = el.find(_EXT_TAG)
    if ext is None:
        return None, None, None
    gov = ext.find(_GOV_TAG)
    if gov is None:
        return None, None, None
    action_key = gov.get("actionRegistryKey") or None
    profile_id = gov.get("assignedRoleProfileId") or None
    raw_tier = gov.get("autonomyTier")
    explicit_tier = int(raw_tier) if raw_tier and raw_tier.strip().isdigit() else None
    return action_key, profile_id, explicit_tier


def _default_tier(step_type: str, action_registry_key: str | None) -> int:
    """The default autonomy tier for an actionable element (see module docstring)."""
    if step_type == "user":
        return 1
    if step_type == "send":
        return 1
    if step_type == "business_rule":
        return 3
    if step_type == "service":
        action = REGISTRY.get(action_registry_key) if action_registry_key else None
        access = getattr(action, "access_type", None)
        if access == "read":
            return 3  # read-only actions are safe to run fully autonomously
        # WRITE-capable OR unknown/unresolved: confirm-and-log. A Tier-3 write is
        # a deliberate authoring choice (explicit autonomyTier), never a default.
        return 2
    raise DeriverError(f"no tier rule for step_type {step_type!r}")


def derive_steps(bpmn_xml: str) -> list[dict]:
    """Parse ``bpmn_xml`` and return the ordered list of derived step dicts.

    Reuses Phase 1's ``parse_bpmn`` to prove the XML is an executable process,
    then walks the raw XML for actionable elements + governance extensions.
    Does not touch the database.
    """
    # 1) Validate executability via Phase 1's parser (raises on malformed XML).
    _spec, process_id = parse_bpmn(bpmn_xml)

    # 2) Walk the raw XML of the matching <bpmn:process> for actionable elements.
    root = etree.fromstring(bpmn_xml.encode("utf-8"))
    proc = next(
        (p for p in root.findall("{%s}process" % BPMN_NS) if p.get("id") == process_id),
        None,
    )
    if proc is None:
        raise DeriverError(f"executable process {process_id!r} not found in BPMN XML")

    steps: list[dict] = []
    seen: set[str] = set()
    for el in proc.iter():
        step_type = ACTIONABLE_TYPES.get(etree.QName(el).localname)
        if step_type is None:
            continue
        step_key = el.get("id")
        if not step_key:
            raise DeriverError(f"{etree.QName(el).localname} element is missing its id")
        if step_key in seen:
            raise DeriverError(f"duplicate element id {step_key!r} in process")
        seen.add(step_key)

        action_key, profile_id, explicit_tier = _governance(el)
        tier = explicit_tier if explicit_tier is not None else _default_tier(step_type, action_key)
        steps.append(
            {
                "step_key": step_key,
                "step_type": step_type,
                "autonomy_tier": tier,
                "action_registry_key": action_key,
                "assigned_role_profile_id": profile_id,
                "display_name": el.get("name"),
            }
        )
    return steps


def _as_uuid(value: str | None) -> UUID | None:
    if value is None:
        return None
    try:
        return UUID(value)
    except (ValueError, AttributeError, TypeError) as exc:
        raise DeriverError(f"assigned_role_profile_id {value!r} is not a valid UUID") from exc


async def derive_and_store_steps(conn, workflow_version_id, org_id, bpmn_xml: str) -> list[dict]:
    """Derive steps from ``bpmn_xml`` and INSERT one ``workflow_steps`` row each.

    The caller owns the transaction (the generator wraps definition + version +
    steps in one).  Returns the derived step dicts.
    """
    steps = derive_steps(bpmn_xml)
    for s in steps:
        await conn.execute(
            """
            INSERT INTO workflow_steps
                (workflow_version_id, org_id, step_key, step_type, autonomy_tier,
                 assigned_role_profile_id, action_registry_key, display_name)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            ON CONFLICT (workflow_version_id, step_key) DO NOTHING
            """,
            workflow_version_id,
            org_id,
            s["step_key"],
            s["step_type"],
            s["autonomy_tier"],
            _as_uuid(s["assigned_role_profile_id"]),
            s["action_registry_key"],
            s["display_name"],
        )
    return steps
