"""DEALROOM2 — the firm template library.

Templates live in ``agreement_templates``: platform rows (scope 'platform',
org_id NULL — Super Admin only) and firm rows (scope 'org', org_id = the firm —
its org admins, ``manage_org_settings``). A firm row starts as a copy of a
platform template (or blank), is edited while ``draft``, and is published
(``draft → active``); publishing a version retires the firm's previous active
version of the same key. A version that has left draft, or that any deal
agreement uses, is never edited — a change is a NEW version (the
``agreement_templates_guard`` trigger refuses content edits outside draft, and
this module refuses edits to an in-use draft). Old versions are kept forever;
agreements already published keep their own immutable snapshot
(``agreement_versions``) in any case.

Who may USE a firm template on a deal: publishers, delegates and staff of that
firm, and only once it is ``active``. Another firm never sees it (RLS + the
explicit org filter here).
"""

from __future__ import annotations

import json
import re
from uuid import UUID

from services import deal_room_templates as T
from services.deal_room import DealRoomError, _uuid

KEY_RE = re.compile(r"^[a-z][a-z0-9_]{2,60}$")
SOURCES = (T.SOURCE_PUBLISHER, T.SOURCE_DEAL, T.SOURCE_DEAL_RECORD, T.SOURCE_RECIPIENT, T.SOURCE_SIGNING)
TYPES = ("text", "number", "flag")
COLS = ("id, key, version, title, body, fields, scope, org_id, status, source_path, based_on_id, created_at, "
        "published_at")


def _row(r) -> dict:
    d = dict(r)
    if isinstance(d.get("fields"), str):
        d["fields"] = json.loads(d["fields"])
    return d


def normalize_fields(fields, body: str) -> list[dict]:
    """Validate a field list against the body: every {{field}} in the body is
    declared, every declared field is used, each has a known source/type."""
    if not isinstance(fields, list):
        raise DealRoomError(422, "fields must be a list")
    out, seen = [], set()
    for f in fields:
        if not isinstance(f, dict) or not isinstance(f.get("key"), str) or not KEY_RE.match(f["key"]):
            raise DealRoomError(422, f"Each field needs a lower_snake_case key ({f!r:.60}).")
        k = f["key"]
        if k in seen:
            raise DealRoomError(422, f"Field {k} is declared twice.")
        seen.add(k)
        src = f.get("source") or T._source_for(k, "")
        typ = f.get("type") or "text"
        if src not in SOURCES or typ not in TYPES:
            raise DealRoomError(422, f"Field {k}: source must be one of {', '.join(SOURCES)}; type one of "
                                     f"{', '.join(TYPES)}.")
        out.append({"key": k, "label": (f.get("label") or T.label_of(k)).strip(), "source": src, "type": typ,
                    "default": f.get("default"), "required": bool(f.get("required", typ != "flag")),
                    "description": f.get("description")})
    used = T.body_field_keys(body)
    undeclared = sorted(used - seen)
    if undeclared:
        raise DealRoomError(422, f"The text uses undeclared field(s): {', '.join(undeclared)}")
    unused = sorted(seen - used)
    if unused:
        raise DealRoomError(422, f"Declared field(s) the text never uses: {', '.join(unused)}")
    return out


async def get(conn, template_id) -> dict | None:
    r = await conn.fetchrow(f"SELECT {COLS} FROM agreement_templates WHERE id = $1", _uuid(template_id))
    return _row(r) if r else None


async def in_use(conn, template_id) -> int:
    return int(await conn.fetchval("SELECT count(*) FROM deal_agreements WHERE template_id = $1", _uuid(template_id)))


async def library(conn, org_id) -> list[dict]:
    """Platform templates + this firm's own (never another firm's, even for a
    Super Admin reading through the carve-out)."""
    rows = await conn.fetch(
        f"""
        SELECT {COLS}, (SELECT count(*) FROM deal_agreements a WHERE a.template_id = t.id) AS in_use
        FROM agreement_templates t WHERE t.org_id IS NULL OR t.org_id = $1
        ORDER BY t.scope DESC, t.key, t.version
        """, _uuid(org_id))
    return [_row(r) for r in rows]


async def usable(conn, org_id, *, include_firm: bool) -> list[dict]:
    """What a deal may require: platform (not retired) + the firm's ACTIVE
    templates (only for callers who publish / manage deals in the firm)."""
    rows = await conn.fetch(
        f"""
        SELECT {COLS} FROM agreement_templates
        WHERE (org_id IS NULL AND status <> 'retired')
           OR ($2::boolean AND org_id = $1 AND status = 'active')
        ORDER BY scope DESC, key, version
        """, _uuid(org_id), include_firm)
    return [_row(r) for r in rows]


async def usable_for_deal(conn, template_id, deal_org_id) -> dict | None:
    t = await get(conn, template_id)
    if t is None:
        return None
    if t["org_id"] is None:
        return t if t["status"] != "retired" else None
    return t if (str(t["org_id"]) == str(deal_org_id) and t["status"] == "active") else None


async def _next_version(conn, key: str, org_id) -> int:
    return int(await conn.fetchval(
        "SELECT COALESCE(max(version), 0) + 1 FROM agreement_templates WHERE key = $1 "
        "AND org_id IS NOT DISTINCT FROM $2", key, _uuid(org_id)))


async def create(conn, *, org_id, key: str, title: str, body: str, fields, based_on=None, actor) -> dict:
    """A new template, version 1, draft. org_id None = a platform template."""
    if not isinstance(key, str) or not KEY_RE.match(key):
        raise DealRoomError(422, "key must be lower_snake_case, 3–61 characters.")
    if not (title or "").strip() or not (body or "").strip():
        raise DealRoomError(422, "A template needs a title and text.")
    flds = normalize_fields(fields, body)
    if await _next_version(conn, key, org_id) != 1:
        raise DealRoomError(409, f"A template with key {key} already exists here — add a version to it instead.")
    r = await conn.fetchrow(
        f"""
        INSERT INTO agreement_templates (key, version, title, body, fields, scope, org_id, status, based_on_id,
                                         created_by)
        VALUES ($1, 1, $2, $3, $4::jsonb, $5, $6, 'draft', $7, $8) RETURNING {COLS}
        """, key, title.strip(), body, json.dumps(flds), "platform" if org_id is None else "org", _uuid(org_id),
        _uuid(based_on), _uuid(actor))
    return _row(r)


async def copy_to_firm(conn, *, org_id, source_id, key: str | None, actor) -> dict:
    src = await get(conn, source_id)
    if src is None or not (src["org_id"] is None or str(src["org_id"]) == str(org_id)):
        raise DealRoomError(404, "Template not found")
    return await create(conn, org_id=org_id, key=key or f"firm_{src['key']}"[:61], title=src["title"],
                        body=src["body"], fields=src["fields"], based_on=src["id"], actor=actor)


async def edit(conn, template: dict, changes: dict) -> dict:
    if template["status"] != "draft":
        raise DealRoomError(409, f"Version {template['version']} is {template['status']} — its text is fixed. "
                                 "Create a new version to change it.")
    if await in_use(conn, template["id"]):
        raise DealRoomError(409, "This version is already used by a deal — create a new version to change it.")
    body = changes.get("body", template["body"])
    fields = normalize_fields(changes["fields"] if "fields" in changes else template["fields"], body)
    title = (changes.get("title") or template["title"]).strip()
    r = await conn.fetchrow(
        f"UPDATE agreement_templates SET title = $2, body = $3, fields = $4::jsonb WHERE id = $1 RETURNING {COLS}",
        template["id"], title, body, json.dumps(fields))
    return _row(r)


async def add_version(conn, template: dict, *, actor) -> dict:
    """The next version of the same key (draft), copied from the latest."""
    open_draft = await conn.fetchval(
        "SELECT version FROM agreement_templates WHERE key = $1 AND org_id IS NOT DISTINCT FROM $2 "
        "AND status = 'draft' AND version > $3", template["key"], template["org_id"], template["version"])
    if open_draft:
        raise DealRoomError(409, f"Version {open_draft} is already an open draft — edit that one.")
    latest = await conn.fetchrow(
        f"SELECT {COLS} FROM agreement_templates WHERE key = $1 AND org_id IS NOT DISTINCT FROM $2 "
        "ORDER BY version DESC LIMIT 1", template["key"], template["org_id"])
    latest = _row(latest)
    n = int(latest["version"]) + 1
    r = await conn.fetchrow(
        f"""
        INSERT INTO agreement_templates (key, version, title, body, fields, scope, org_id, status, based_on_id,
                                         created_by)
        VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, 'draft', $8, $9) RETURNING {COLS}
        """, latest["key"], n, latest["title"], latest["body"], json.dumps(latest["fields"]), latest["scope"],
        latest["org_id"], latest["id"], _uuid(actor))
    return _row(r)


async def publish(conn, template: dict, *, actor) -> dict:
    if template["status"] == "active":
        return template
    if template["status"] != "draft":
        raise DealRoomError(409, "A retired version stays retired.")
    normalize_fields(template["fields"], template["body"])
    await conn.execute(
        "UPDATE agreement_templates SET status = 'retired' WHERE key = $1 AND org_id IS NOT DISTINCT FROM $2 "
        "AND status = 'active' AND id <> $3", template["key"], template["org_id"], template["id"])
    r = await conn.fetchrow(
        f"UPDATE agreement_templates SET status = 'active', published_at = now(), published_by = $2 WHERE id = $1 "
        f"RETURNING {COLS}", template["id"], _uuid(actor))
    return _row(r)


def jsonable(t: dict) -> dict:
    out = {}
    for k, v in t.items():
        out[k] = str(v) if isinstance(v, UUID) else (v.isoformat() if hasattr(v, "isoformat") else v)
    return out
