"""BUGFIX1 (DASH-02) — where each dashboard to-do is resolved.

A to-do row names what it is about (related_type / related_id, or for older
rows only its source and title); this maps that to the page where the member
or staff user acts on it. One function, used by the brief's to-do blocks and
GET /dashboard/todos, so a to-do is a link wherever it is listed. Returns None
only when nothing on the row says where it belongs.
"""
from __future__ import annotations

from urllib.parse import quote

STUB_TITLE_PREFIX = "Complete profile: "


def todo_href(row) -> str | None:
    d = dict(row)
    rtype = d.get("related_type")
    rid = d.get("related_id")
    source = d.get("source")
    action = d.get("action_key")
    if rtype == "entity" and rid:
        return f"/crm/{rid}"
    if source == "entity_stub":
        # Rows written before BUGFIX1 carry no entity id — the stub's name is in
        # the title, and the CRM list searches by it.
        title = d.get("title") or ""
        name = title[len(STUB_TITLE_PREFIX):] if title.startswith(STUB_TITLE_PREFIX) else title
        return f"/crm?q={quote(name)}" if name else "/crm"
    if rtype in ("workflow_run", "workflow_run_step"):
        return "/admin/workflows/runs"
    if rtype == "workflow_trigger":
        return "/admin/workflows/triggers"
    if rtype == "org_settings":
        return "/admin/settings"
    if isinstance(action, str) and action.startswith("/"):
        return action
    if rtype in ("spv_subscription", "spv_document"):
        return "/spvs"
    return None
