"""Score an extraction run against the GOLD SET (goldset.structural, Task 5).

For every (note, field) where the gold set has a CURRENT value (valid_to IS
NULL) and the run covered the note:

    correct   gold and run agree (both absent counts as correct)
    wrong     both have a value and they differ
    missed    gold has a value, the run has none
    false     gold says absent, the run has a value
    accuracy  correct / (correct + wrong + missed + false)

Agreement uses the cascade's own normalisation (schema.normalize): a RANGE
compares min and max (the bound wording is not scored); a LIST uses the
notefields list comparison (members validated, normalised field by field, and
sorted by identity — a reordered list of the same members agrees).

A field with fewer than MIN_NOTES_TO_SCORE reviewed notes is reported as
"too few" and never scored. THE GATE passes when every critical field is at or
above GATE_ACCURACY on at least GATE_MIN_NOTES reviewed notes; otherwise the
report lists the fields below the gate and why.

The run's resolved values: its STAGED fields when it staged any (pilot,
evaluation, cascade). A gold_prefill run stages nothing, so its values are its
readings: the rules reading for a rules field, the derived reading for a
derived field, the model reading otherwise (latest wins).
"""
from __future__ import annotations

import json
from collections import defaultdict

from services.database import platform_scope
from services.note_extraction.schema import (
    METHOD_DERIVED, METHOD_RULES, FieldSpec, normalize,
)

MIN_NOTES_TO_SCORE = 10
GATE_ACCURACY = 0.95
GATE_MIN_NOTES = 40
FIELD_SETS = ("critical", "core", "all")


def select_fields(specs: list[FieldSpec], which: str) -> list[FieldSpec]:
    """critical = the registry's critical fields; core = every field outside the
    rules-only section that is not derived (what pricing and cost comparison
    need, NOTE_FIELDS.md's principle); all = every live field."""
    if which not in FIELD_SETS:
        raise ValueError(f"--fields must be one of {FIELD_SETS}")
    if which == "critical":
        return [s for s in specs if s.critical]
    if which == "core":
        return [s for s in specs if s.critical or (s.section != "rules_only" and s.extraction_method != METHOD_DERIVED)]
    return list(specs)


def classify(spec: FieldSpec, gold_value, run_value) -> str:
    g = normalize(spec, gold_value)
    r = normalize(spec, run_value)
    if g is None and r is None:
        return "correct"
    if g is None:
        return "false"
    if r is None:
        return "missed"
    return "correct" if g == r else "wrong"


def score(specs: list[FieldSpec], gold: dict[tuple[str, str], object], run: dict[tuple[str, str], object],
          covered_notes: set[str]) -> dict:
    """Pure. gold: {(note, field): value or None(absent)}; run: {(note, field):
    value}; covered_notes: the notes the run covered. Returns the report."""
    by_key = {s.key: s for s in specs}
    counts: dict[str, dict] = {s.key: {"correct": 0, "wrong": 0, "missed": 0, "false": 0, "notes": 0}
                               for s in specs}
    reviewed_notes: set[str] = set()
    examples: dict[str, list] = defaultdict(list)
    for (note, fk), gv in gold.items():
        spec = by_key.get(fk)
        if spec is None or note not in covered_notes:
            continue
        reviewed_notes.add(note)
        outcome = classify(spec, gv, run.get((note, fk)))
        c = counts[fk]
        c[outcome] += 1
        c["notes"] += 1
        if outcome != "correct" and len(examples[fk]) < 3:
            examples[fk].append({"note": note, "outcome": outcome, "gold": gv, "run": run.get((note, fk))})
    fields = {}
    for s in specs:
        c = counts[s.key]
        n = c["notes"]
        too_few = n < MIN_NOTES_TO_SCORE
        fields[s.key] = {**c, "critical": s.critical, "section": s.section,
                         "accuracy": None if too_few or n == 0 else c["correct"] / n,
                         "status": "too few" if too_few else "scored",
                         "examples": examples.get(s.key, [])}
    crit = [s.key for s in specs if s.critical]
    scored_crit = [k for k in crit if fields[k]["status"] == "scored"]
    crit_n = sum(fields[k]["notes"] for k in scored_crit)
    crit_correct = sum(fields[k]["correct"] for k in scored_crit)
    below = []
    for k in crit:
        f = fields[k]
        if f["notes"] < GATE_MIN_NOTES:
            below.append({"field": k, "reason": f"only {f['notes']} reviewed notes (gate needs {GATE_MIN_NOTES})",
                          "accuracy": f["accuracy"], "notes": f["notes"]})
        elif f["accuracy"] is None or f["accuracy"] < GATE_ACCURACY:
            below.append({"field": k, "reason": f"accuracy {f['accuracy']:.3f} < {GATE_ACCURACY}",
                          "accuracy": f["accuracy"], "notes": f["notes"]})
    gate_pass = bool(crit) and not below
    return {
        "reviewed_notes": len(reviewed_notes),
        "fields": fields,
        "critical_accuracy": (crit_correct / crit_n) if crit_n else None,
        "critical_fields_scored": len(scored_crit),
        "critical_fields_too_few": [k for k in crit if fields[k]["status"] == "too few"],
        "gate": {"result": "PASS" if gate_pass else "FAIL", "accuracy": GATE_ACCURACY,
                 "min_notes": GATE_MIN_NOTES, "below": below},
        "rules": {"min_notes_to_score": MIN_NOTES_TO_SCORE},
    }


# ── Loaders ─────────────────────────────────────────────────────────────────
def _val(v):
    return json.loads(v) if isinstance(v, str) else v


async def load_gold(conn, *, batch: str | None = None) -> dict[tuple[str, str], object]:
    async with platform_scope(conn):
        rows = await conn.fetch(
            """SELECT g.reference_filing_id, g.field_key, g.value
                 FROM portfolio.note_gold_values g
                WHERE g.valid_to IS NULL
                  AND ($1::text IS NULL OR g.reference_filing_id IN
                       (SELECT reference_filing_id FROM portfolio.note_gold_candidates WHERE sample_batch = $1))""",
            batch)
    return {(str(r["reference_filing_id"]), r["field_key"]): _val(r["value"]) for r in rows}


async def load_run(conn, run_id, specs: list[FieldSpec]) -> tuple[dict, set[str], str]:
    """(values {(note, field): value}, covered notes, source 'staging'|'readings')."""
    async with platform_scope(conn):
        staged = await conn.fetch(
            """SELECT s.reference_filing_id, f.field_key, f.resolved_value
                 FROM portfolio.note_extraction_staging s
                 JOIN portfolio.note_extraction_staged_fields f ON f.staging_id = s.id
                WHERE s.run_id = $1""", run_id)
        notes = await conn.fetch(
            "SELECT DISTINCT reference_filing_id FROM portfolio.note_extraction_staging WHERE run_id = $1", run_id)
    if notes:
        values = {(str(r["reference_filing_id"]), r["field_key"]): _val(r["resolved_value"]) for r in staged}
        return values, {str(r["reference_filing_id"]) for r in notes}, "staging"
    method = {s.key: s.extraction_method for s in specs}
    async with platform_scope(conn):
        rows = await conn.fetch(
            """SELECT reference_filing_id, field_key, value, source, status
                 FROM portfolio.note_term_readings
                WHERE run_id = $1 AND field_key <> '__call__'
                ORDER BY created_at, id""", run_id)
        covered = await conn.fetch(
            "SELECT DISTINCT reference_filing_id FROM portfolio.note_term_readings WHERE run_id = $1", run_id)
    values: dict = {}
    for r in rows:
        fk = r["field_key"]
        m = method.get(fk)
        if m is None or r["status"] != "ok":
            continue
        want = "rules" if m == METHOD_RULES else "derived" if m == METHOD_DERIVED else None
        src = r["source"]
        if want is not None and src != want:
            continue
        if want is None and src not in ("model_1", "model_2", "escalation", "jev", "derived"):
            continue
        key = (str(r["reference_filing_id"]), fk)
        v = _val(r["value"])
        if v is None and key in values:
            continue   # a later null never erases an earlier stated value
        values[key] = v
    return values, {str(r["reference_filing_id"]) for r in covered}, "readings"


async def write_report(conn, run_id, report: dict) -> None:
    """Merge the score into the run's report jsonb under 'gold_score'."""
    async with platform_scope(conn):
        n = await conn.fetchval(
            """UPDATE portfolio.note_extraction_runs
                  SET report = COALESCE(report, '{}'::jsonb) || jsonb_build_object('gold_score', $2::jsonb)
                WHERE id = $1 RETURNING 1""", run_id, json.dumps(report, default=str))
    if n is None:
        raise LookupError(f"run {run_id}: no row updated — it does not exist, or the write lacked "
                          f"super-admin context")


async def score_run(conn, run_id, specs: list[FieldSpec], *, batch: str | None = None,
                    fields: str = "critical", write: bool = True) -> dict:
    async with platform_scope(conn):
        run = await conn.fetchrow("SELECT id, run_kind, config FROM portfolio.note_extraction_runs WHERE id = $1",
                                  run_id)
    if run is None:
        raise LookupError(f"run {run_id} not found")
    chosen = select_fields(specs, fields)
    gold = await load_gold(conn, batch=batch)
    values, covered, source = await load_run(conn, run_id, specs)
    report = score(chosen, gold, values, covered)
    cfg = _val(run["config"]) or {}
    report.update({"run_id": str(run_id), "run_kind": run["run_kind"], "batch": batch, "fields": fields,
                   "run_values_from": source, "notes_in_run": len(covered),
                   "field_results": report.pop("fields")})
    async with platform_scope(conn):
        prefill_models = sorted({r["m"] for r in await conn.fetch(
            "SELECT DISTINCT config->>'model' AS m FROM portfolio.note_extraction_runs "
            "WHERE run_kind = 'gold_prefill' AND config->>'model' IS NOT NULL")})
    cfg_text = json.dumps(cfg, default=str)
    anchored = run["run_kind"] == "gold_prefill" or any(m and m in cfg_text for m in prefill_models)
    report["anchoring"] = {
        "prefill_models": prefill_models,
        "anchored": anchored,
        "caveat": ("The gold set was pre-filled by " + (", ".join(prefill_models) or "gpt-5-mini")
                   + "; a pre-fill model's own score against it is biased upward and must be reported "
                     "with that caveat."),
    }
    if write:
        await write_report(conn, run_id, report)
    return report
