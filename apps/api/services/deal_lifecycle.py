"""BUGFIX1 (MKT-01 / DASH-01 / MKT-03 / R2-MKT-05) — ONE deal lifecycle.

Before this module a deal's state lived in two independent columns:
``deals.deal_status`` (the ``deal_status`` enum, written by Submit / Archive)
and ``deals.deal_stage`` (free text, default 'sourced', written only by the
generic edit and the never-rendered "Advance" button). The badge and the
dashboard read the first; the Marketplace Stage column and summary read the
second; neither matched the ``deal_stages`` config, whose keys are
``deal_stage_1..6``. So every deal showed two vocabularies.

The rule now:

* **State** is ``deals.deal_status`` — the enum, constrained by the database,
  moved only by ``apply_status`` (transitions below).
* **Order and labels** come from the org's ``deal_stages`` config (Rule 1):
  its rows, by ``display_order``, name the enum values in enum order (Sourced =
  draft, Submitted, Under Review, Active, Closed, Archived). ``lifecycle()``
  pairs them and refuses to guess when the counts differ.
* ``deals.deal_stage`` is kept as a MIRROR holding the config key of the
  current status, written in the same statement as ``deal_status`` — so the
  legacy stage readers (filter, notifications) can never disagree again.
* Every state change is bi-temporal on the SYSTEM axis (deals has live FKs —
  member_investments, spvs, deal_documents, deal_votes — so the id must stay
  stable): the prior version is copied with ``system_to = now()`` and
  ``slug = NULL`` (slug is UNIQUE; the copy is history, not addressable), then
  the live row is updated. The copy also gives Un-archive the prior stage.
"""
from __future__ import annotations

from uuid import UUID

from services.audit import write_audit_log
from services.org_settings import get_setting

# Allowed transitions: from -> set(to). "any -> archived" is always permitted
# (with confirmation); "archived -> <its prior status>" is Un-archive only.
STATUS_TRANSITIONS = {
    "draft": {"submitted"},
    "submitted": {"under_review"},
    "under_review": {"active"},
    "active": {"closed"},
}
ARCHIVED = "archived"
# The statuses an ordinary member may see (GET /deals, /deals/{id}). ONE copy:
# routers/marketplace re-exports it; Document Search filters deal attachments by it.
MEMBER_VISIBLE_STATUSES = ("active", "under_review")
SUBMITTED = "submitted"


class LifecycleError(Exception):
    """A refused lifecycle step; ``status`` is the HTTP code, ``detail`` the message."""

    def __init__(self, status: int, detail):
        super().__init__(str(detail))
        self.status = status
        self.detail = detail


async def enum_statuses(conn) -> list[str]:
    rows = await conn.fetch(
        "SELECT enumlabel FROM pg_enum WHERE enumtypid = 'deal_status'::regtype "
        "ORDER BY enumsortorder"
    )
    return [r["enumlabel"] for r in rows]


async def lifecycle(conn, org_id) -> list[dict]:
    """The ordered lifecycle: [{status, key, label, order}] — one entry per enum
    value. Labels and keys from the org's ``deal_stages`` config; when the org
    has no such config (or it does not have one row per enum value) the label is
    None and ``key`` is None, and the UI shows the status value itself."""
    statuses = await enum_statuses(conn)
    cfg = await conn.fetch(
        """
        SELECT config_key, config_value FROM config
        WHERE org_id = $1 AND category = 'deal_stages' AND COALESCE(is_active, true)
        ORDER BY display_order NULLS LAST, config_key
        """,
        org_id,
    )
    paired = len(cfg) == len(statuses)
    out = []
    for i, st in enumerate(statuses):
        out.append({
            "status": st,
            "key": cfg[i]["config_key"] if paired else None,
            "label": cfg[i]["config_value"] if paired else None,
            "order": i + 1,
        })
    return out


def label_map(stages: list[dict]) -> dict:
    """status -> display label (the status value itself when unconfigured)."""
    return {s["status"]: (s["label"] or s["status"]) for s in stages}


def key_for(stages: list[dict], status: str):
    return next((s["key"] for s in stages if s["status"] == status), None)


def status_for_key(stages: list[dict], key: str):
    """A config key (deal_stage_N) or a raw status -> the status, or None."""
    for s in stages:
        if key in (s["key"], s["status"]):
            return s["status"]
    return None


async def summary(conn, org_id, *, member_statuses=None) -> list[dict]:
    """Counts per lifecycle stage, in lifecycle order, zero-filled — the ONE
    query behind the Marketplace summary bar AND the dashboard snapshot.
    ``member_statuses`` narrows to what a non-staff caller may see."""
    stages = await lifecycle(conn, org_id)
    params: list = [org_id]
    where = "org_id = $1 AND valid_to IS NULL AND system_to IS NULL"
    if member_statuses is not None:
        params.append(list(member_statuses))
        where += f" AND deal_status::text = ANY(${len(params)}::text[])"
    rows = await conn.fetch(
        f"SELECT deal_status::text AS status, count(*) AS n FROM deals WHERE {where} "
        f"GROUP BY deal_status",
        *params,
    )
    counts = {r["status"]: r["n"] for r in rows}
    visible = set(member_statuses) if member_statuses is not None else None
    return [
        {"status": s["status"], "stage": s["key"] or s["status"],
         "label": s["label"] or s["status"], "order": s["order"],
         "count": int(counts.get(s["status"], 0))}
        for s in stages
        if visible is None or s["status"] in visible
    ]


async def archive_version(conn, org_id, deal_id) -> UUID | None:
    """Copy the live row as a closed SYSTEM-axis version (id kept on the live
    row). Every column is carried — the pre-BUGFIX1 copy in update_deal omitted
    deal_stage and kept the slug, which violates deals_slug_key, so it could
    never have succeeded (live: zero deal history rows)."""
    return await conn.fetchval(
        """
        INSERT INTO deals (
            org_id, slug, name, description, deal_status, deal_stage,
            asset_super_class, asset_class, asset_sub_category,
            sponsor_entity_id, sponsor_name_override, target_raise,
            minimum_investment, expected_return_pct, term_months,
            deal_date, close_date, location, highlights, tags,
            is_featured, submitted_by, reviewed_by, published_at,
            valid_from, valid_to, system_from, system_to,
            created_by, created_at, updated_at, publisher_entity_id
        )
        SELECT org_id, NULL, name, description, deal_status, deal_stage,
               asset_super_class, asset_class, asset_sub_category,
               sponsor_entity_id, sponsor_name_override, target_raise,
               minimum_investment, expected_return_pct, term_months,
               deal_date, close_date, location, highlights, tags,
               is_featured, submitted_by, reviewed_by, published_at,
               valid_from, valid_to, system_from, now(),
               created_by, created_at, updated_at, publisher_entity_id
        FROM deals
        WHERE id = $1 AND org_id = $2 AND valid_to IS NULL AND system_to IS NULL
        RETURNING id
        """,
        deal_id, org_id,
    )


async def missing_for_submit(conn, org_id, deal: dict) -> list[dict]:
    """The configured required fields this deal lacks: [{field, label}]."""
    required = await get_setting(conn, org_id, "marketplace.submit_required_fields") or []
    missing = []
    for item in required:
        field = item.get("field") if isinstance(item, dict) else item
        label = (item.get("label") if isinstance(item, dict) else None) or field
        if field not in deal:
            continue  # a configured field this table does not have is not the deal's fault
        value = deal.get(field)
        if value is None or (isinstance(value, str) and not value.strip()) or (
            isinstance(value, (list, tuple)) and not value
        ):
            missing.append({"field": field, "label": label})
    return missing


async def prior_status(conn, org_id, deal_id) -> str | None:
    """The status an archived deal held before it was archived.

    First the newest closed system-axis version that is not archived (every
    archive made through apply_status leaves one); then, for a deal archived by
    the old in-place UPDATE (no version), the audit trail's status_change
    rows — the `deal_status` of the last change before the newest archive."""
    # A closed version carries the live row's created_at (archive_version
    # copies it), which is how a version is tied back to its deal — the copy
    # gets its own id and deals has no lineage column.
    v = await conn.fetchval(
        """
        SELECT h.deal_status::text FROM deals h
        JOIN deals live ON live.id = $2 AND live.org_id = $1
        WHERE h.org_id = $1 AND h.id <> live.id AND h.system_to IS NOT NULL
          AND h.created_at = live.created_at
          AND h.created_by IS NOT DISTINCT FROM live.created_by
          AND h.deal_status::text <> 'archived'
        ORDER BY h.system_to DESC LIMIT 1
        """,
        org_id, deal_id,
    )
    if v:
        return v
    rows = await conn.fetch(
        """
        SELECT payload->>'deal_status' AS st, payload->>'from' AS frm
        FROM audit_log
        WHERE resource_id = $1 AND action = 'status_change'
        ORDER BY created_at DESC
        """,
        deal_id,
    )
    seen_archive = False
    for r in rows:
        if r["st"] == ARCHIVED and not seen_archive:
            if r["frm"] and r["frm"] != ARCHIVED:
                return r["frm"]
            seen_archive = True
            continue
        if seen_archive and r["st"] and r["st"] != ARCHIVED:
            return r["st"]
    if seen_archive:
        # Archived with no earlier change on record: it was archived from its
        # creation status, which insert_deal always sets to draft.
        created = await conn.fetchval(
            "SELECT payload->>'deal_status' FROM audit_log WHERE resource_id = $1 "
            "AND action = 'create' ORDER BY created_at LIMIT 1",
            deal_id,
        )
        return created
    return None


async def apply_status(conn, org_id, deal_id, target: str, *, actor=None,
                       confirm: bool = False, select: str = "*",
                       unarchive: bool = False) -> dict:
    """Move one deal to ``target`` (in the caller's transaction). Raises
    LifecycleError on a refused step. Returns the updated live row."""
    current = await conn.fetchrow(
        f"SELECT * FROM deals WHERE id = $1 AND org_id = $2 "
        f"AND valid_to IS NULL AND system_to IS NULL FOR UPDATE",
        deal_id, org_id,
    )
    if current is None:
        raise LifecycleError(404, "Deal not found")
    src = current["deal_status"]
    stages = await lifecycle(conn, org_id)
    if target not in {s["status"] for s in stages}:
        raise LifecycleError(400, f"Unknown deal status: {target}")

    if unarchive:
        if src != ARCHIVED:
            raise LifecycleError(400, "Only an archived deal can be un-archived.")
        prior = await prior_status(conn, org_id, deal_id)
        if not prior or prior == ARCHIVED:
            raise LifecycleError(409, "The deal's stage before it was archived is not on record.")
        target = prior
    else:
        allowed = STATUS_TRANSITIONS.get(src, set()) | {ARCHIVED}
        if src == ARCHIVED or target not in allowed:
            raise LifecycleError(400, f"Invalid transition: {src} -> {target}")
        if target in (ARCHIVED, SUBMITTED) and not confirm:
            label = label_map(stages)[target]
            raise LifecycleError(
                428, f"Confirmation required: moving this deal to {label} needs confirm=true.")
        if target == SUBMITTED:
            missing = await missing_for_submit(conn, org_id, dict(current))
            if missing:
                names = ", ".join(m["label"] for m in missing)
                raise LifecycleError(422, {
                    "message": f"Before submitting for review, complete: {names}.",
                    "missing_fields": missing,
                })

    version_id = await archive_version(conn, org_id, deal_id)
    params: list = [deal_id, org_id, target, key_for(stages, target)]
    sets = ["deal_status = $3::deal_status", "deal_stage = COALESCE($4, $3::text)",
            "system_from = now()", "updated_at = now()"]
    if target == "active" and current["published_at"] is None:
        sets.append("published_at = now()")
    # Un-archive RESTORES a stage; it is not a new submission, so submitted_by
    # keeps whoever really submitted.
    if target == SUBMITTED and actor is not None and not unarchive:
        params.append(actor)
        sets.append(f"submitted_by = ${len(params)}")
    updated = await conn.fetchrow(
        f"UPDATE deals SET {', '.join(sets)} WHERE id = $1 AND org_id = $2 "
        f"AND valid_to IS NULL AND system_to IS NULL RETURNING {select}",
        *params,
    )
    if updated is None:
        # Never claim a cause we cannot know (CLAUDE.md): zero rows is either a
        # vanished row or an RLS-refused write.
        raise LifecycleError(409, "The deal could not be updated (no live row matched).")
    await write_audit_log(
        conn, org_id=org_id, action="status_change", table_name="deals", record_id=deal_id,
        old={"deal_status": src},
        new={"deal_status": target, "from": src, "version_id": str(version_id) if version_id else None,
             "unarchive": bool(unarchive)},
        actor=actor,
    )
    return dict(updated)
