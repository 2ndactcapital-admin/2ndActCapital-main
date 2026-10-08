"""noteextractb1 — gold-set review, evaluation results and distribution
participants. SUPER ADMIN ONLY, not org-scoped (the note corpus is global).

    GET  /admin/note-extraction/gold/candidates                 sampler proposals (?batch=, ?status=) + progress
    GET  /admin/note-extraction/gold/notes/{filing_id}          document text + every reading + pre-fill + gold
    PUT  /admin/note-extraction/gold/notes/{filing_id}/fields/{field_key}   confirm / correct / absent
    POST /admin/note-extraction/gold/notes/{filing_id}/skip     skip the note, with a reason
    GET  /admin/note-extraction/runs                            evaluation + pilot runs (results grid)
    GET  /admin/note-extraction/runs/{run_id}                   one run's full report
    GET  /admin/note-extraction/participants                    participants reference data
    POST /admin/note-extraction/participants                    add a participant
    PUT  /admin/note-extraction/participants/{participant_id}   edit type / aliases / status

The gate is ``services.rbac.is_super_admin`` — checked FIRST, the one shared
helper. An org admin and a member get 403 on every route. Gold values are
written only through services.note_extraction.gold.record_gold_value with the
SIGNED-IN reviewer's id; no request body carries a reviewer or an org_id
(extra='forbid').
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from services.database import get_pool
from services.note_extraction import documents, gold, participants as parts, prefill, schema, trim
from services.rbac import is_super_admin, load_principal
from services.users import ensure_user

router = APIRouter(tags=["admin", "note-extraction"])

PERMISSION = "super_admin"
GOLD_EDITABLE = ["value", "action", "notes"]


async def _require_super_admin(request: Request) -> str:
    pool = await get_pool()
    async with pool.acquire() as conn:
        actor_id = await ensure_user(conn, request)
        principal = await load_principal(conn, actor_id)
    if not is_super_admin(principal):
        raise HTTPException(status_code=403, detail="Super Admin access required")
    return str(actor_id)


def _envelope(can_write: bool, editable: list[str]) -> dict:
    return {
        "permissions": {"can_read": True, "can_write": can_write, "is_super_admin": True,
                        "read_permission": PERMISSION, "write_permission": PERMISSION},
        "vocabularies": {"editable": editable if can_write else [],
                         "inline_editable": editable if can_write else []},
    }


async def _specs(conn) -> list[schema.FieldSpec]:
    return schema.build_field_specs(await schema.load_registry_rows(conn))


def _spec_out(s: schema.FieldSpec) -> dict:
    return {"key": s.key, "label": s.label, "kind": s.kind, "enum": list(s.enum) if s.enum else None,
            "critical": s.critical, "description": s.description, "origin": s.origin,
            "section": s.section, "value_shape": s.value_shape, "unit": s.unit,
            "extraction_method": s.extraction_method, "trap_rule": s.trap_rule}


# ── Gold ────────────────────────────────────────────────────────────────────
class GoldBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str = Field(pattern="^(confirmed|corrected|absent)$")
    value: Any = None
    source_reading_id: str | None = None
    notes: str | None = Field(default=None, max_length=2000)


@router.get("/admin/note-extraction/gold/candidates")
async def gold_candidates(request: Request, status: str | None = Query(default=None),
                          batch: str | None = Query(default=None, max_length=200)):
    await _require_super_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await gold.list_candidates(conn, status=status, batch=batch)
        progress = await gold.batch_progress(conn, batch)
        batches = await gold.batches(conn)
    return {"rows": rows, "batches": batches, "batch": batch, "progress": progress,
            "anchoring_note": prefill.anchoring_note("gpt-5-mini"), **_envelope(True, GOLD_EDITABLE)}


# Sections a reviewer works through first, expanded; every other section starts
# collapsed. A section holding a critical field is always expanded too.
GOLD_FIRST_SECTIONS = ("economics", "distribution")
SECTION_ORDER = ("identity", "dates", "underlyings", "payoff", "income", "calls", "economics", "distribution",
                 "rules_only")


def _member_fields(list_key: str) -> list[dict]:
    """One list member's editable fields, from the member model (schema.py)."""
    from typing import get_args

    out = []
    for name, f in schema.LIST_MEMBER_MODELS[list_key].model_fields.items():
        args = [a for a in (get_args(f.annotation) or (f.annotation,)) if a is not type(None)]
        enum = None
        kind = "text"
        if args and all(isinstance(a, str) for a in args):      # a bare Literal[...]
            kind, enum = "enum", list(args)
            args = []
        for a in args:
            lit = get_args(a)
            if lit and all(isinstance(x, str) for x in lit):
                kind, enum = "enum", list(lit)
            elif a is schema.Decimal:
                kind = "number"
            elif a is schema.date:
                kind = "date"
        out.append({"key": name, "kind": kind, "enum": enum, "required": f.is_required()})
    return out


def _gold_field(s: schema.FieldSpec) -> dict:
    d = _spec_out(s)
    if s.kind == schema.KIND_LIST:
        d["members"] = _member_fields(s.key)
    if s.kind == schema.KIND_RANGE:
        d["range_bounds"] = list(schema.RANGE_BOUNDS)
    return d


def _sections(specs: list[schema.FieldSpec]) -> list[dict]:
    present = []
    for s in specs:
        if s.section not in present:
            present.append(s.section)
    order = [x for x in SECTION_ORDER if x in present] + [x for x in present if x not in SECTION_ORDER]
    crit = {s.section for s in specs if s.critical}
    first = [x for x in order if x in crit or x in GOLD_FIRST_SECTIONS]
    rest = [x for x in order if x not in first]
    return [{"key": x, "expanded": x in first,
             "fields": [s.key for s in sorted((s for s in specs if s.section == x),
                                              key=lambda s: (not s.critical, s.sort_order or 0))]}
            for x in first + rest]


_PREFILL_SOURCE = {schema.METHOD_RULES: ("rules",), schema.METHOD_DERIVED: ("derived",),
                   schema.METHOD_MODEL: ("model_1", "derived")}


def _prefill_by_field(specs, readings, run_id: str | None) -> dict:
    """The pre-fill reading per field: from the newest gold_prefill run, the
    reading of the field's own extraction method (rules / model / derived)."""
    if not run_id:
        return {}
    out = {}
    for s in specs:
        want = _PREFILL_SOURCE.get(s.extraction_method, ("model_1",))
        cands = [r for r in readings if r["run_id"] == run_id and r["field_key"] == s.key
                 and r["source"] in want and r["status"] == "ok"]
        cands.sort(key=lambda r: (want.index(r["source"]), r["value"] is None))
        if cands:
            r = cands[0]
            out[s.key] = {"reading_id": r["id"], "value": r["value"], "quote": r["source_quote"],
                          "source": r["source"],
                          "came_from": {"rules": "rules", "derived": "derivation"}.get(r["source"], "model"),
                          "quote_verified": r["quote_verified"], "text_start": r.get("text_start"),
                          "text_end": r.get("text_end")}
    return out


@router.get("/admin/note-extraction/gold/notes/{filing_id}")
async def gold_note(request: Request, filing_id: str):
    await _require_super_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await documents.load_filing_row(conn, filing_id)
        if row is None:
            raise HTTPException(status_code=404, detail="filing not found")
        specs = await _specs(conn)
        live = {s.key for s in specs}
        readings = [r for r in await gold.readings_for(conn, filing_id) if r["field_key"] in live]
        golds = [g for g in await gold.gold_values(conn, filing_id) if g["field_key"] in live]
        prefill_runs = await gold.prefill_runs_for(conn, filing_id)
        candidate = next(iter(await gold.list_candidates_for(conn, filing_id)), None)
        status = await gold.refresh_candidate_status(conn, filing_id, live, update=False) if candidate else None
        try:
            doc = await documents.load_document(conn, filing_id)
            text, text_error = doc.text, None
            for r in readings:
                span = doc.index.locate(r["source_quote"]) if r.get("source_quote") else None
                r["text_start"], r["text_end"] = (span if span else (None, None))
            for g in golds:
                span = doc.index.locate(g["source_quote"]) if g.get("source_quote") else None
                g["text_start"], g["text_end"] = (span if span else (None, None))
            trimmed = trim.trim(doc.text)
            tokens = {"full": trimmed.full_tokens_est, "trimmed": trimmed.trimmed_tokens_est}
        except Exception as exc:  # noqa: BLE001
            text, text_error, tokens = None, f"{type(exc).__name__}: {exc}", None
    run_id = prefill_runs[0]["run_id"] if prefill_runs else None
    return {
        "filing": {"id": str(row["id"]), "filer_name": row["filer_name"], "form_type": row["form_type"],
                   "accession_number": row["accession_number"],
                   "filing_date": row["filing_date"].isoformat() if row["filing_date"] else None},
        "candidate": candidate,
        "progress": status,
        "fields": [_gold_field(s) for s in specs],
        "sections": _sections(specs),
        "text": text, "text_error": text_error, "tokens": tokens,
        "readings": readings, "gold": golds,
        "prefill": {"runs": prefill_runs, "run_id": run_id,
                    "by_field": _prefill_by_field(specs, readings, run_id)},
        "anchoring_note": prefill.anchoring_note(prefill_runs[0]["model"] if prefill_runs and prefill_runs[0]["model"]
                                                 else "gpt-5-mini"),
        **_envelope(True, GOLD_EDITABLE),
    }


@router.put("/admin/note-extraction/gold/notes/{filing_id}/fields/{field_key}")
async def put_gold_value(request: Request, filing_id: str, field_key: str, body: GoldBody):
    reviewer_id = await _require_super_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        if await documents.load_filing_row(conn, filing_id) is None:
            raise HTTPException(status_code=404, detail="filing not found")
        live = await _specs(conn)
        specs = {s.key: s for s in live}
        spec = specs.get(field_key)
        if spec is None:
            raise HTTPException(status_code=422, detail=f"unknown or retired field '{field_key}'")
        quote = start = end = None
        if body.source_reading_id:
            if body.action != "confirmed":
                raise HTTPException(status_code=422, detail="source_reading_id is set only when confirming a reading")
            r = await conn.fetchrow(
                "SELECT source_quote, raw_char_start, raw_char_end FROM portfolio.note_term_readings "
                "WHERE id = $1 AND reference_filing_id = $2 AND field_key = $3",
                body.source_reading_id, filing_id, field_key)
            if r is None:
                raise HTTPException(status_code=422, detail="source_reading_id is not a reading of this field")
            quote, start, end = r["source_quote"], r["raw_char_start"], r["raw_char_end"]
        try:
            saved = await gold.record_gold_value(
                conn, reviewer_id=reviewer_id, reference_filing_id=filing_id, spec=spec,
                action=body.action, value=body.value, source_reading_id=body.source_reading_id,
                source_quote=quote, raw_char_start=start, raw_char_end=end, notes=body.notes)
        except gold.GoldWriteError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        progress = await gold.refresh_candidate_status(conn, filing_id, specs)
    return {"saved": saved, "progress": progress, **_envelope(True, GOLD_EDITABLE)}


class SkipBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=3, max_length=1000)


@router.post("/admin/note-extraction/gold/notes/{filing_id}/skip")
async def skip_gold_note(request: Request, filing_id: str, body: SkipBody):
    actor = await _require_super_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            row = await gold.skip_candidate(conn, filing_id, reason=body.reason, actor_id=actor)
        except gold.GoldWriteError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
    if row is None:
        raise HTTPException(status_code=404, detail="no gold candidate for that filing")
    return {"candidate": row, **_envelope(True, GOLD_EDITABLE)}


# ── Results grid ────────────────────────────────────────────────────────────
def _run_out(r) -> dict:
    d = dict(r)
    for k in ("id", "ensemble_config_id", "created_by"):
        d[k] = str(d[k]) if d.get(k) else None
    for k in ("started_at", "finished_at"):
        d[k] = d[k].isoformat() if d.get(k) else None
    for k in ("spend_cap_usd", "spent_usd"):
        d[k] = float(d[k]) if d.get(k) is not None else None
    for k in ("config", "report"):
        if isinstance(d.get(k), str):
            d[k] = json.loads(d[k])
    return d


@router.get("/admin/note-extraction/runs")
async def list_runs(request: Request, run_kind: str | None = Query(default=None)):
    await _require_super_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, run_kind, status, ensemble_config_id, config, spend_cap_usd, spent_usd,
                      notes_planned, notes_done, stop_reason, report, created_by, started_at, finished_at
                 FROM portfolio.note_extraction_runs
                WHERE run_kind <> 'verify' AND ($1::text IS NULL OR run_kind = $1)
                ORDER BY started_at DESC LIMIT 100""", run_kind)
    return {"rows": [_run_out(r) for r in rows], **_envelope(False, [])}


@router.get("/admin/note-extraction/runs/{run_id}")
async def get_run(request: Request, run_id: str):
    await _require_super_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        r = await conn.fetchrow("SELECT * FROM portfolio.note_extraction_runs WHERE id = $1", run_id)
    if r is None:
        raise HTTPException(status_code=404, detail="run not found")
    return {"run": _run_out(r), **_envelope(False, [])}


# ── Participants ────────────────────────────────────────────────────────────
class ParticipantCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    canonical_name: str = Field(min_length=2, max_length=200)
    participant_type: str = Field(pattern="^(issuer_affiliate|distribution_platform|dealer|wealth_manager|other)$")
    aliases: list[str] = Field(default_factory=list)
    notes: str | None = None


class ParticipantUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    participant_type: str | None = Field(
        default=None, pattern="^(issuer_affiliate|distribution_platform|dealer|wealth_manager|other)$")
    aliases: list[str] | None = None
    status: str | None = Field(default=None, pattern="^(proposed|active|retired)$")
    notes: str | None = None


PARTICIPANT_EDITABLE = ["participant_type", "aliases", "status", "notes"]


@router.get("/admin/note-extraction/participants")
async def list_participants(request: Request):
    await _require_super_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, canonical_name, participant_type, aliases, status, observed_count, notes, "
            "created_at, updated_at FROM portfolio.distribution_participants ORDER BY observed_count DESC, canonical_name")
    out = []
    for r in rows:
        d = dict(r)
        d["id"] = str(d["id"])
        d["created_at"] = d["created_at"].isoformat()
        d["updated_at"] = d["updated_at"].isoformat()
        out.append(d)
    return {"rows": out, "participant_types": list(parts.PARTICIPANT_TYPES),
            **_envelope(True, PARTICIPANT_EDITABLE)}


@router.post("/admin/note-extraction/participants")
async def add_participant(request: Request, body: ParticipantCreate):
    actor = await _require_super_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
            row = await conn.fetchrow(
                """INSERT INTO portfolio.distribution_participants
                     (canonical_name, participant_type, aliases, status, notes, created_by)
                   VALUES ($1, $2, $3, 'active', $4, $5)
                   ON CONFLICT (lower(canonical_name)) DO NOTHING RETURNING id""",
                body.canonical_name.strip(), body.participant_type, body.aliases, body.notes, actor)
    if row is None:
        raise HTTPException(status_code=409, detail="a participant with that name already exists")
    return {"id": str(row["id"]), **_envelope(True, PARTICIPANT_EDITABLE)}


@router.put("/admin/note-extraction/participants/{participant_id}")
async def update_participant(request: Request, participant_id: str, body: ParticipantUpdate):
    await _require_super_admin(request)
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=422, detail="no changes")
    sets = ", ".join(f"{k} = ${i}" for i, k in enumerate(changes, start=2))
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
            row = await conn.fetchrow(
                f"UPDATE portfolio.distribution_participants SET {sets}, updated_at = now() "
                f"WHERE id = $1 RETURNING id", participant_id, *changes.values())
    if row is None:
        raise HTTPException(status_code=404,
                            detail="no participant updated: it does not exist, or the write lacked super-admin context")
    return {"id": str(row["id"]), **_envelope(True, PARTICIPANT_EDITABLE)}
