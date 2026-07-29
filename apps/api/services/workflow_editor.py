"""Manual BPMN edit → new workflow version (Workflow Manager — Phase 3).

Phase 2 could only ever *generate* a brand-new definition (``generate_workflow``
is INSERT-only by construction). Phase 3 adds the second, complementary write
path: an Org Admin opens a definition's CURRENT version in the diagram editor,
edits the BPMN (and per-step governance) through the properties panel, and saves.

The audit discipline mirrors the rest of the platform (``ownership_change_log``,
bi-temporal writes): a save NEVER mutates an existing ``workflow_versions`` row.
It always creates a NEW version:

  * ``version_number`` = previous max + 1
  * the new row is ``is_current = true``; the previously-current row is flipped
    to ``is_current = false``
  * ``workflow_steps`` are RE-DERIVED for the NEW version only (via Phase 2's
    ``derive_and_store_steps``). The old version's steps are left untouched as a
    historical record.

Validation reuses Phase 2 verbatim (``validate_workflow_bpmn``): the XML must
parse via SpiffWorkflow AND every referenced action key / profile id must
resolve to a real row. Invalid XML is rejected loudly and NOTHING is written.

``org_id`` is always supplied by the caller from the authenticated context and
every read/write is scoped to it — never trusted from a request body.
"""
from __future__ import annotations

from services.workflow_nl_generator import validate_workflow_bpmn
from services.workflow_steps_deriver import derive_and_store_steps


class WorkflowEditError(Exception):
    """The target definition does not exist for this org."""


class WorkflowValidationError(Exception):
    """The edited BPMN failed validation — no new version was stored."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


async def save_new_version(
    pool, *, definition_id, org_id, bpmn_xml: str, created_by, change_summary: str | None = None
) -> dict:
    """Validate ``bpmn_xml`` and persist it as a new current version.

    Returns ``{workflow_version_id, version_number, step_count}``. Raises
    ``WorkflowEditError`` (unknown definition) or ``WorkflowValidationError``
    (bad BPMN) BEFORE any row is written.
    """
    if not bpmn_xml or not bpmn_xml.strip():
        raise WorkflowValidationError(["bpmn_xml is required"])

    # 1) Confirm the definition is real and belongs to this org, then validate
    #    the edited XML the exact way Phase 2 validates generated XML.
    async with pool.acquire() as conn:
        exists = await conn.fetchval(
            "SELECT 1 FROM workflow_definitions WHERE id = $1 AND org_id = $2",
            definition_id, org_id,
        )
        if not exists:
            raise WorkflowEditError("workflow definition not found for org")
        errors = await validate_workflow_bpmn(conn, org_id, bpmn_xml)
    if errors:
        raise WorkflowValidationError(errors)

    # 2) Persist only now that the BPMN is fully validated. One transaction:
    #    close the old current version, insert the new one, derive its steps.
    async with pool.acquire() as conn:
        async with conn.transaction():
            prev_number = await conn.fetchval(
                """
                SELECT max(version_number) FROM workflow_versions
                WHERE workflow_definition_id = $1 AND org_id = $2
                """,
                definition_id, org_id,
            )
            next_number = (prev_number or 0) + 1

            await conn.execute(
                """
                UPDATE workflow_versions SET is_current = false
                WHERE workflow_definition_id = $1 AND org_id = $2 AND is_current = true
                """,
                definition_id, org_id,
            )
            version_id = await conn.fetchval(
                """
                INSERT INTO workflow_versions
                    (workflow_definition_id, org_id, version_number, bpmn_xml,
                     change_summary, is_current, created_by)
                VALUES ($1, $2, $3, $4, $5, true, $6)
                RETURNING id
                """,
                definition_id, org_id, next_number, bpmn_xml,
                change_summary or "Manual edit via diagram editor", created_by,
            )
            # Re-derive steps for the NEW version only; the old version's
            # workflow_steps rows are deliberately never touched.
            steps = await derive_and_store_steps(conn, version_id, org_id, bpmn_xml)
            await conn.execute(
                "UPDATE workflow_definitions SET updated_at = now() WHERE id = $1",
                definition_id,
            )

    return {
        "workflow_version_id": version_id,
        "version_number": next_number,
        "step_count": len(steps),
    }
