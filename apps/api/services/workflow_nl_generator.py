"""Natural-language → BPMN workflow generation (Workflow Manager — Phase 2).

Turns a plain-English process description into a governed, executable workflow:
a ``workflow_definitions`` row + its first ``workflow_versions`` row (BPMN XML) +
the ``workflow_steps`` derived from that XML (via ``workflow_steps_deriver``).

Discipline (mirrors the document classifier's closed-reference-list matching):
the model may reference ONLY real Profiles and real action-registry keys that
exist for the org — never a plausible-sounding invention.  We hand it the exact
closed lists, and then we *validate* the output against those lists before a
single row is written; an invalid reference triggers one error-correction retry,
then a hard, loud failure.  Unvalidated BPMN is never stored.

AI access routes through the central ``call_claude_text`` helper, which since
Sprint 27 walks the org's per-org model fallback chain and logs every call to
``ai_decision_log`` (the TaskRouter mechanism).  This module never touches the
Anthropic SDK directly.

TASK 4 — "generate once, never regenerate" is enforced STRUCTURALLY: the public
entry point ``generate_workflow`` has no ``workflow_definition_id`` /
``workflow_version_id`` parameter and contains no UPDATE of any existing
``workflow_versions.bpmn_xml``.  It can only INSERT a brand-new definition; there
is no code path that can target or overwrite an existing one.
"""
from __future__ import annotations

import re

from services.action_registry import REGISTRY
from services.extraction import call_claude_text
from services.workflow_steps_deriver import derive_steps, derive_and_store_steps

# Keep the generator's extension convention identical to the deriver's.
from services.workflow_steps_deriver import EXT_NS

_TASK_TYPE = "workflow_generation"


class WorkflowGenerationError(Exception):
    """Raised when generation cannot produce validated BPMN (after one retry)."""


# ── org context (closed reference lists) ─────────────────────────────────────
async def _fetch_profiles(conn, org_id) -> list[dict]:
    rows = await conn.fetch(
        "SELECT id, name, description FROM profiles WHERE org_id = $1 ORDER BY name",
        org_id,
    )
    return [dict(r) for r in rows]


def _registry_actions() -> list[dict]:
    return [
        {"key": a.key, "access_type": a.access_type, "description": a.description}
        for a in REGISTRY.all()
    ]


# ── prompt construction ──────────────────────────────────────────────────────
_MAPPING_TABLE = """PRIMITIVE → BPMN mapping (use these element types only):
  • a human decision / approval / review     → <bpmn:userTask>
  • an automated system action               → <bpmn:serviceTask> (must carry a governance actionRegistryKey)
  • a notification / message to a member     → <bpmn:sendTask>
  • a branch / decision point                → <bpmn:exclusiveGateway>
  • process entry / exit                     → <bpmn:startEvent> / <bpmn:endEvent>
Connect every node with <bpmn:sequenceFlow> and matching <bpmn:incoming>/<bpmn:outgoing>."""


def _build_system_prompt(profiles: list[dict], actions: list[dict]) -> str:
    action_lines = "\n".join(
        f"    - {a['key']}  ({a['access_type']}-only) — {a['description']}" for a in actions
    ) or "    (none registered)"
    profile_lines = "\n".join(
        f"    - {p['id']}  — {p['name']}" for p in profiles
    ) or "    (none seeded)"
    return f"""You are a BPMN 2.0 process designer for 2nd Act Capital, a private
membership investment platform. Convert the member's natural-language process
description into a single valid, executable BPMN 2.0 XML document.

{_MAPPING_TABLE}

Embed governance metadata on serviceTask / userTask / sendTask elements using a
standard BPMN extension block (namespace xmlns:twoa="{EXT_NS}"):

  <bpmn:extensionElements>
    <twoa:governance actionRegistryKey="..." assignedRoleProfileId="..."/>
  </bpmn:extensionElements>

HARD RULES — violating any of these makes the output unusable:
  1. Output ONLY the raw BPMN XML. No markdown, no code fences, no commentary.
  2. Exactly one <bpmn:process ... isExecutable="true"> with one startEvent and
     at least one endEvent, all nodes reachable via sequenceFlows.
  3. Every <bpmn:serviceTask> MUST set actionRegistryKey to a key drawn VERBATIM
     from this exact list — never invent or paraphrase a key:
{action_lines}
  4. Any assignedRoleProfileId MUST be a UUID drawn VERBATIM from this exact list
     of real Profiles — never invent a UUID:
{profile_lines}
  5. Use only the real ids/keys above. If no listed action fits an automated
     step, model it as a userTask instead of inventing an action.
  6. Give every element a stable, unique id attribute."""


def _user_prompt(description: str) -> str:
    return f"Process to model:\n\n{description.strip()}\n\nReturn the BPMN XML now."


def _correction_prompt(errors: list[str]) -> str:
    bullets = "\n".join(f"  - {e}" for e in errors)
    return (
        "Your previous BPMN was rejected by validation for these reasons:\n"
        f"{bullets}\n\n"
        "Regenerate the COMPLETE corrected BPMN XML. Use ONLY action keys and "
        "profile UUIDs from the lists you were given. Output raw XML only."
    )


# ── XML extraction + reference validation ────────────────────────────────────
def _extract_xml(text: str) -> str | None:
    """Pull the BPMN document out of a model response (tolerate stray fences)."""
    if not text:
        return None
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else ""
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    match = re.search(r"<(?:\w+:)?definitions\b.*</(?:\w+:)?definitions>", t, re.DOTALL)
    if match:
        return match.group(0).strip()
    return t.strip() or None


def _validate(xml: str | None, valid_action_keys: set[str], valid_profile_ids: set[str]) -> list[str]:
    """Return a list of validation errors ([] means valid).

    Validity means: parses as an executable SpiffWorkflow process AND every
    embedded action key / profile id resolves to a REAL existing row.
    """
    if not xml:
        return ["model returned no BPMN XML"]
    try:
        steps = derive_steps(xml)  # runs Phase-1 parse_bpmn + derives steps
    except Exception as exc:  # parse or structural failure
        return [f"BPMN did not parse / derive: {exc}"]

    errors: list[str] = []
    for s in steps:
        key = s["action_registry_key"]
        if s["step_type"] == "service" and not key:
            errors.append(f"serviceTask '{s['step_key']}' is missing an actionRegistryKey")
        if key and (key not in valid_action_keys or REGISTRY.get(key) is None):
            errors.append(f"unknown actionRegistryKey '{key}' on element '{s['step_key']}'")
        prof = s["assigned_role_profile_id"]
        if prof and prof not in valid_profile_ids:
            errors.append(f"unknown assignedRoleProfileId '{prof}' on element '{s['step_key']}'")
    return errors


async def _generate_once(system, messages, org_id, valid_action_keys, valid_profile_ids):
    text = await call_claude_text(
        system, messages, max_tokens=2000, org_id=org_id, task_type=_TASK_TYPE
    )
    if text is None:
        return None, ["AI unavailable (no API key configured or model chain exhausted)"]
    xml = _extract_xml(text)
    return xml, _validate(xml, valid_action_keys, valid_profile_ids)


def _derive_name(description: str) -> str:
    first = description.strip().splitlines()[0].strip() if description.strip() else "Untitled workflow"
    return (first[:117] + "…") if len(first) > 118 else first


# ── public entry point ───────────────────────────────────────────────────────
async def generate_workflow(pool, *, org_id, description: str, created_by, name: str | None = None) -> dict:
    """Generate a BRAND-NEW workflow from a natural-language description.

    Creates a ``workflow_definitions`` row, its ``workflow_versions`` v1
    (``is_current=true``) and the derived ``workflow_steps`` — all in one
    transaction — and returns their ids plus the validated BPMN.

    TASK 4 (structural): this signature has NO ``workflow_definition_id`` /
    ``workflow_version_id`` parameter, and the body only ever INSERTs. There is
    deliberately no code path that can target, revision, or overwrite an existing
    definition or its stored ``bpmn_xml`` — regeneration is impossible by
    construction, not merely by policy.

    ``org_id`` is supplied by the caller from the authenticated context, never
    from a request body.
    """
    if not description or not description.strip():
        raise WorkflowGenerationError("description is required")

    async with pool.acquire() as conn:
        profiles = await _fetch_profiles(conn, org_id)
    actions = _registry_actions()
    valid_action_keys = {a["key"] for a in actions}
    valid_profile_ids = {str(p["id"]) for p in profiles}

    system = _build_system_prompt(profiles, actions)
    messages = [{"role": "user", "content": _user_prompt(description)}]

    xml, errors = await _generate_once(system, messages, org_id, valid_action_keys, valid_profile_ids)
    if errors:
        # One error-correction retry, echoing the model's attempt + the failures.
        retry_messages = messages + [
            {"role": "assistant", "content": xml or "(no output)"},
            {"role": "user", "content": _correction_prompt(errors)},
        ]
        xml, errors = await _generate_once(
            system, retry_messages, org_id, valid_action_keys, valid_profile_ids
        )
    if errors:
        raise WorkflowGenerationError(
            "generation failed validation after one retry: " + "; ".join(errors)
        )

    # Persist only now that the BPMN is fully validated.
    async with pool.acquire() as conn:
        async with conn.transaction():
            definition_id = await conn.fetchval(
                """
                INSERT INTO workflow_definitions (org_id, name, description, created_by)
                VALUES ($1, $2, $3, $4)
                RETURNING id
                """,
                org_id, name or _derive_name(description), description, created_by,
            )
            version_id = await conn.fetchval(
                """
                INSERT INTO workflow_versions
                    (workflow_definition_id, org_id, version_number, bpmn_xml,
                     change_summary, is_current, created_by)
                VALUES ($1, $2, 1, $3, 'AI-generated from natural language', true, $4)
                RETURNING id
                """,
                definition_id, org_id, xml, created_by,
            )
            steps = await derive_and_store_steps(conn, version_id, org_id, xml)

    return {
        "workflow_definition_id": definition_id,
        "workflow_version_id": version_id,
        "bpmn_xml": xml,
        "steps": steps,
    }
