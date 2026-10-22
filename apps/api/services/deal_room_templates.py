"""DEALROOM1 — agreement templates: load from the docs file, render, seed.

The source of truth for the two Phase-1 agreements is the counsel-review DRAFT
``docs/templates/deal_room_templates_v1.md``. ``parse_templates`` reads it into
(key, version, title, body, fields); ``seed_platform_templates`` writes
``platform_nda`` v1 and ``platform_joinder`` v1 into ``agreement_templates``
(scope 'platform', org_id NULL, status 'draft') with the file section's SHA-256,
so a seeded row can always be traced back to — and re-derived from — the file.

Body syntax after loading:
  {{field}}                          substitution
  {{#if flag}} … {{else}} … {{/if}}  the file's two "[optional: …]" notes,
                                     converted mechanically (see _convert_optional)

Rendering is a pure function (``render``) over a value map; ``missing_fields``
names every required field that has no value, by its label — the message that
blocks publishing an agreement (publisher side) or opening it (recipient side).
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import re
from datetime import date

REPO = pathlib.Path(__file__).resolve().parents[3]
TEMPLATE_FILE = REPO / "docs" / "templates" / "deal_room_templates_v1.md"
TEMPLATE_REL = "docs/templates/deal_room_templates_v1.md"

# Where each field's value comes from at render time.
SOURCE_PUBLISHER = "publisher"   # deal settings, prefilled from the publisher's CRM record
SOURCE_DEAL = "deal"             # deal settings the publisher sets (with the file's defaults)
SOURCE_DEAL_RECORD = "deal_record"  # the deal row itself (deal_name)
SOURCE_RECIPIENT = "recipient"   # the investing entity's CRM record and the signer
SOURCE_SIGNING = "signing"       # set at signing (signature_date)

# Fields the file lets be empty ("…_clause" fields drop to nothing) and the
# on/off switches (always have a value: their default).
OPTIONAL_FIELDS = {"recipient_entity_clause", "recipient_signatory_title_clause"}


class TemplateError(ValueError):
    pass


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def label_of(key: str) -> str:
    """A field's display label, derived from its key (stored with the template)."""
    words = key.replace("_", " ").strip()
    words = re.sub(r"\bnda\b", "NDA", words)
    return words[:1].upper() + words[1:]


def _source_for(key: str, desc: str) -> str:
    if key == "signature_date":
        return SOURCE_SIGNING
    if key == "deal_name":
        return SOURCE_DEAL_RECORD
    if key.startswith("recipient_"):
        return SOURCE_RECIPIENT
    if key.startswith("publisher_"):
        return SOURCE_PUBLISHER
    return SOURCE_DEAL


def _parse_fields(lines: list[str]) -> list[dict]:
    """The file's ``Fields:`` bullets → field definitions, in file order."""
    out: list[dict] = []
    for ln in lines:
        ln = ln.strip()
        if not ln.startswith("- "):
            continue
        spec, _, desc = ln[2:].partition(" — ")
        for m in re.finditer(r"([a-z_]+)(?:\s*\(([^)]*)\))?", spec):
            key, attrs = m.group(1), (m.group(2) or "")
            if not key or key in ("default", "on", "off"):
                continue
            is_flag = "on/off" in attrs
            default = None
            dm = re.search(r"default\s+(\"[^\"]*\"|[^,)]+)", attrs)
            if dm:
                raw = dm.group(1).strip().strip('"')
                if is_flag:
                    default = raw.lower() == "on"
                elif raw.isdigit():
                    default = int(raw)
                else:
                    default = raw
            elif is_flag:
                default = False
            out.append({
                "key": key,
                "label": label_of(key),
                "source": _source_for(key, desc),
                "type": "flag" if is_flag else ("number" if isinstance(default, int) else "text"),
                "default": default,
                "required": key not in OPTIONAL_FIELDS and not is_flag,
                "description": desc.strip() or None,
            })
    seen, uniq = set(), []
    for f in out:
        if f["key"] not in seen:
            seen.add(f["key"])
            uniq.append(f)
    return uniq


def _convert_optional(body: str) -> str:
    """The two "[optional: …]" notes in the file → {{#if}} blocks.

    1. "[optional: include when {{X}} is on]" inside a numbered paragraph: the
       whole paragraph is kept only when X is on.
    2. "[optional: when {{X}} is off, replace the last sentence with "…"]":
       the sentence before the note becomes {{#if X}}sentence{{else}}…{{/if}}.
    """
    paras = re.split(r"\n\s*\n", body)
    for i, p in enumerate(paras):
        m = re.search(r"\s*\[optional: include when \{\{(\w+)\}\} is on\]", p)
        if m:
            p = p[:m.start()] + p[m.end():]
            paras[i] = "{{#if " + m.group(1) + "}}" + p + "{{/if}}"
    body = "\n\n".join(paras)

    pat = re.compile(r"\s*\[optional: when\s+\{\{(\w+)\}\}\s+is off, replace the last sentence with\s+\"(.*?)\"\]",
                     re.S)
    m = pat.search(body)
    if m:
        before = body[:m.start()]
        # the last sentence = after the previous sentence end (". ") or line break
        cut = max(before.rfind(". "), before.rfind(".\n"))
        start = cut + 2 if cut >= 0 else 0
        sentence = before[start:]
        alt = re.sub(r"\s+", " ", m.group(2)).strip()
        body = (before[:start] + "{{#if " + m.group(1) + "}}" + sentence + "{{else}}" + alt + "{{/if}}"
                + body[m.end():])
    if "[optional:" in body:
        raise TemplateError("an [optional: …] note in the template file was not understood")
    return body


def parse_templates(text: str | None = None) -> list[dict]:
    """Every TEMPLATE section of the file, in order."""
    if text is None:
        if not TEMPLATE_FILE.exists():
            raise TemplateError(f"{TEMPLATE_REL} is absent — the operator places it before Task 4")
        text = TEMPLATE_FILE.read_text(encoding="utf-8")
    sections = re.split(r"^## TEMPLATE \d+ — ", text, flags=re.M)[1:]
    out = []
    for sec in sections:
        sec = sec.split("\n---", 1)[0]
        title_line, _, rest = sec.partition("\n")
        km = re.search(r"^key:\s*(\w+)\s*·\s*version:\s*(\d+)\s*$", rest, re.M)
        if not km:
            raise TemplateError(f"template {title_line!r} has no 'key: … · version: …' line")
        after = rest[km.end():]
        body_part, _, fields_part = after.partition("\nFields:")
        body = _convert_optional(body_part.strip("\n").rstrip())
        out.append({
            "key": km.group(1),
            "version": int(km.group(2)),
            "title": _title_case(title_line.strip()),
            "body": body,
            "fields": _parse_fields(fields_part.splitlines()),
            "source_sha256": _sha(sec),
        })
    return out


def _title_case(s: str) -> str:
    small = {"and", "to", "of", "the", "a"}
    words = s.lower().split()
    def cap(w):
        return "-".join(part[:1].upper() + part[1:] for part in w.split("-"))
    return " ".join(w if (i and w in small) else cap(w) for i, w in enumerate(words))


# ── Rendering (pure) ────────────────────────────────────────────────────────
_IF = re.compile(r"\{\{#if (\w+)\}\}(.*?)(?:\{\{else\}\}(.*?))?\{\{/if\}\}", re.S)
_VAR = re.compile(r"\{\{(\w+)\}\}")


def _truthy(v) -> bool:
    if isinstance(v, str):
        return v.strip().lower() not in ("", "off", "false", "no", "0")
    return bool(v)


def body_field_keys(body: str) -> set[str]:
    return (set(_VAR.findall(body)) - {"else"}) | {m.group(1) for m in _IF.finditer(body)}


def missing_fields(template: dict, values: dict, *, sources=None) -> list[dict]:
    """Required fields (optionally only those from ``sources``) with no value.
    A field inside an {{#if}} branch that will not render is not required."""
    body = _resolve_ifs(template["body"], values)
    used = set(_VAR.findall(body))
    missing = []
    for f in _fields(template):
        if sources is not None and f["source"] not in sources:
            continue
        if not f.get("required") or f["key"] not in used:
            continue
        v = values.get(f["key"])
        if v is None or (isinstance(v, str) and not v.strip()):
            missing.append({"key": f["key"], "label": f["label"], "source": f["source"]})
    return missing


def _fields(template: dict) -> list[dict]:
    fields = template.get("fields") or []
    return json.loads(fields) if isinstance(fields, str) else fields


def _resolve_ifs(body: str, values: dict) -> str:
    return _IF.sub(lambda m: m.group(2) if _truthy(values.get(m.group(1))) else (m.group(3) or ""), body)


def render(template: dict, values: dict) -> str:
    """Fill every field. Raises TemplateError naming each missing required field."""
    miss = missing_fields(template, values)
    if miss:
        raise TemplateError("Missing required field(s): " + ", ".join(m["label"] for m in miss))
    body = _resolve_ifs(template["body"], values)

    def sub(m):
        v = values.get(m.group(1))
        return "" if v is None else str(v)

    text = _VAR.sub(sub, body)
    # A dropped optional paragraph leaves a blank gap; normalise to one blank line.
    return re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"


def text_sha256(text: str) -> str:
    return _sha(text)


def defaults(template: dict) -> dict:
    return {f["key"]: f["default"] for f in _fields(template) if f.get("default") is not None}


def format_date(d: date) -> str:
    return f"{d.strftime('%B')} {d.day}, {d.year}"


# ── Seeding ────────────────────────────────────────────────────────────────
async def seed_platform_templates(conn) -> list[dict]:
    """Insert platform_nda / platform_joinder v1 (DRAFT) if absent. Caller holds
    the super-admin RLS carve-out (platform rows have org_id NULL)."""
    out = []
    for t in parse_templates():
        existing = await conn.fetchrow(
            "SELECT id, source_sha256 FROM agreement_templates WHERE key = $1 AND version = $2 AND org_id IS NULL",
            t["key"], t["version"])
        if existing:
            out.append({"key": t["key"], "version": t["version"], "id": str(existing["id"]), "created": False,
                        "matches_file": existing["source_sha256"] == t["source_sha256"]})
            continue
        tid = await conn.fetchval(
            """
            INSERT INTO agreement_templates (key, version, title, body, fields, scope, org_id, status,
                                             source_path, source_sha256)
            VALUES ($1, $2, $3, $4, $5::jsonb, 'platform', NULL, 'draft', $6, $7)
            RETURNING id
            """,
            t["key"], t["version"], t["title"], t["body"], json.dumps(t["fields"]), TEMPLATE_REL,
            t["source_sha256"])
        out.append({"key": t["key"], "version": t["version"], "id": str(tid), "created": True, "matches_file": True})
    return out
