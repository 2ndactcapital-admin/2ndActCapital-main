"""The GOLD SET — hand-checked note values — and the sampler that proposes notes.

ONLY HUMANS WRITE GOLD VALUES. ``record_gold_value`` is the one write path. It
takes the reviewer's user id (the router passes the signed-in super admin's
id, after ``rbac.is_super_admin``) and, inside ONE transaction, sets the
transaction-local ``app.gold_reviewer_id`` GUC that the table's BEFORE trigger
(``note_gold_values_human_guard``) demands, plus the super-admin RLS context.
The cascade, the harness and every model path have no call into this module's
writer; a write without the GUC is refused BY THE DATABASE.

A re-review closes the open row (valid_to) and inserts a new one (Rule 3); the
gold row records the reviewer and the timestamp. The same transaction appends
a 'human' reading, so the readings table holds every value any source —
including a person — produced for the field.

THE SAMPLER proposes 50-100 notes stratified across issuers, years and product
types, and DELIBERATELY includes trap cases found by keyword. Candidates are
written to ``note_gold_candidates`` as 'proposed'; nothing is gold until a
person confirms it.
"""
from __future__ import annotations

import json
import random
import re
from collections import defaultdict
from dataclasses import dataclass

from services.note_extraction.sanitize import strip_nul, strip_nul_deep
from services.note_extraction.schema import FieldSpec, normalize
from services.note_extraction.store import CALL_FIELD

GOLD_ACTIONS = ("confirmed", "corrected", "absent")


class GoldWriteError(ValueError):
    pass


async def record_gold_value(conn, *, reviewer_id: str, reference_filing_id: str, spec: FieldSpec,
                            action: str, value, source_reading_id: str | None = None,
                            source_quote: str | None = None, raw_char_start: int | None = None,
                            raw_char_end: int | None = None, notes: str | None = None) -> dict:
    if action not in GOLD_ACTIONS:
        raise GoldWriteError(f"action must be one of {GOLD_ACTIONS}")
    if action == "absent":
        value = None
    elif value is None:
        raise GoldWriteError("a confirmed or corrected gold value needs a value (use 'absent' for none)")
    if normalize(spec, value) is None and value is not None:
        raise GoldWriteError(f"value {value!r} is not a valid {spec.kind} for '{spec.key}'")
    value = strip_nul_deep(value)
    source_quote = strip_nul(source_quote)
    notes = strip_nul(notes)
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        await conn.execute("SELECT set_config('app.gold_reviewer_id', $1, true)", str(reviewer_id))
        await conn.execute(
            """UPDATE portfolio.note_gold_values SET valid_to = now()
                WHERE reference_filing_id = $1 AND field_key = $2 AND valid_to IS NULL""",
            reference_filing_id, spec.key)
        row = await conn.fetchrow(
            """INSERT INTO portfolio.note_gold_values
                 (reference_filing_id, field_key, value, action, source_reading_id, source_quote,
                  raw_char_start, raw_char_end, notes, reviewer_id)
               VALUES ($1, $2, $3::jsonb, $4, $5, $6, $7, $8, $9, $10)
               RETURNING id, reviewed_at""",
            reference_filing_id, spec.key, None if value is None else json.dumps(value),
            action, source_reading_id, source_quote, raw_char_start, raw_char_end, notes, reviewer_id)
        await conn.execute(
            """INSERT INTO portfolio.note_term_readings
                 (reference_filing_id, field_key, value, value_normalized, source, origin, status,
                  source_quote, raw_char_start, raw_char_end, created_by, metadata)
               VALUES ($1, $2, $3::jsonb, $4, 'human', 'gold_review', 'ok', $5, $6, $7, $8, $9::jsonb)""",
            reference_filing_id, spec.key, None if value is None else json.dumps(value),
            normalize(spec, value), source_quote, raw_char_start, raw_char_end, reviewer_id,
            json.dumps({"gold_value_id": str(row["id"]), "action": action}))
        await conn.execute(
            """UPDATE portfolio.note_gold_candidates SET status = 'in_review', updated_at = now()
                WHERE reference_filing_id = $1 AND status = 'proposed'""", reference_filing_id)
    return {"id": str(row["id"]), "reviewed_at": row["reviewed_at"].isoformat(),
            "field_key": spec.key, "value": value, "action": action, "reviewer_id": str(reviewer_id)}


async def gold_values(conn, reference_filing_id) -> list[dict]:
    rows = await conn.fetch(
        """SELECT g.id, g.field_key, g.value, g.action, g.source_reading_id, g.source_quote,
                  g.reviewer_id, g.reviewed_at, u.email AS reviewer_email
             FROM portfolio.note_gold_values g LEFT JOIN users u ON u.id = g.reviewer_id
            WHERE g.reference_filing_id = $1 AND g.valid_to IS NULL ORDER BY g.field_key""",
        reference_filing_id)
    out = []
    for r in rows:
        d = dict(r)
        d["value"] = json.loads(d["value"]) if d["value"] is not None else None
        for k in ("id", "source_reading_id", "reviewer_id"):
            d[k] = str(d[k]) if d[k] is not None else None
        d["reviewed_at"] = d["reviewed_at"].isoformat()
        out.append(d)
    return out


async def readings_for(conn, reference_filing_id) -> list[dict]:
    rows = await conn.fetch(
        """SELECT id, field_key, value, source, origin, status, deployment_name, provider_model,
                  source_quote, quote_verified, value_in_quote, probability, run_id, created_at
             FROM portfolio.note_term_readings
            WHERE reference_filing_id = $1 AND field_key <> $2
            ORDER BY field_key, created_at""", reference_filing_id, CALL_FIELD)
    out = []
    for r in rows:
        d = dict(r)
        d["value"] = json.loads(d["value"]) if d["value"] is not None else None
        d["id"] = str(d["id"])
        d["run_id"] = str(d["run_id"]) if d["run_id"] else None
        d["probability"] = float(d["probability"]) if d["probability"] is not None else None
        d["created_at"] = d["created_at"].isoformat()
        out.append(d)
    return out


async def list_candidates(conn, *, status: str | None = None, limit: int = 200) -> list[dict]:
    rows = await conn.fetch(
        """SELECT c.id, c.reference_filing_id, c.sample_batch, c.issuer_group, c.filing_year,
                  c.product_type, c.trap_tags, c.status, c.proposed_at,
                  f.filer_name, f.form_type, f.filing_date, f.accession_number,
                  (SELECT count(*) FROM portfolio.note_gold_values g
                    WHERE g.reference_filing_id = c.reference_filing_id AND g.valid_to IS NULL) AS gold_fields
             FROM portfolio.note_gold_candidates c
             JOIN portfolio.reference_filings f ON f.id = c.reference_filing_id
            WHERE ($1::text IS NULL OR c.status = $1)
            ORDER BY c.proposed_at DESC, f.filer_name LIMIT $2""", status, limit)
    out = []
    for r in rows:
        d = dict(r)
        for k in ("id", "reference_filing_id"):
            d[k] = str(d[k])
        d["proposed_at"] = d["proposed_at"].isoformat()
        d["filing_date"] = d["filing_date"].isoformat() if d["filing_date"] else None
        out.append(d)
    return out


# ── The sampler ─────────────────────────────────────────────────────────────
TRAP_PATTERNS: dict[str, re.Pattern] = {
    "threshold_trigger_buffer_wording": re.compile(
        r"downside\s+threshold|trigger\s+(?:level|value|price)|buffer\s+(?:amount|percentage|level)|knock-?in",
        re.I),
    "memory_coupon": re.compile(r"memory|unpaid\s+contingent\s+(?:coupon|interest)|previously\s+unpaid", re.I),
    "worst_of_basket": re.compile(r"worst[- ]of|least\s+performing|lowest\s+performing|each\s+underlying", re.I),
    "daily_observation": re.compile(r"(?:any|each)\s+trading\s+day\s+during|daily\s+(?:closing|observation)|"
                                    r"at\s+any\s+time\s+during", re.I),
    "issuer_call": re.compile(r"(?:redeem|call)\s+the\s+(?:notes|securities)[^.]{0,40}(?:at\s+our\s+option|"
                              r"in\s+(?:our|its)\s+sole\s+discretion)|optional\s+(?:early\s+)?redemption|issuer\s+call",
                              re.I),
    "automatic_call": re.compile(r"automatic(?:ally)?\s+(?:call|redeem|early\s+redemption)", re.I),
    "conditional_principal": re.compile(r"(?:you\s+could|may)\s+lose\s+(?:some|all|a\s+significant)|"
                                        r"repayment\s+of\s+principal\s+(?:is|will\s+be)\s+(?:at\s+risk|contingent)",
                                        re.I),
    "distribution_platform": re.compile(r"iCapital|platform\s+(?:fee|provider)|distribution\s+platform|"
                                        r"marketing\s+(?:fee|support)|structuring\s+fee", re.I),
    "fee_based_account_price": re.compile(r"fee[- ]based\s+(?:advisory\s+)?accounts?[^.]{0,200}price", re.I),
}

PRODUCT_PATTERNS = (
    ("autocallable", re.compile(r"automatic(?:ally)?\s+(?:call|redeem)|auto-?callable", re.I)),
    ("reverse_convertible", re.compile(r"reverse\s+convertible", re.I)),
    ("buffered_note", re.compile(r"\bbuffer", re.I)),
    ("digital", re.compile(r"\bdigital\b|fixed\s+(?:payment|return)\s+if", re.I)),
    ("principal_protected", re.compile(r"principal[- ]protected|100%\s+principal\s+protection", re.I)),
    ("income", re.compile(r"contingent\s+(?:coupon|interest)", re.I)),
)


def trap_tags(text: str) -> list[str]:
    return [k for k, rx in TRAP_PATTERNS.items() if rx.search(text or "")]


def product_type(text: str) -> str:
    for name, rx in PRODUCT_PATTERNS:
        if rx.search(text or ""):
            return name
    return "other"


@dataclass
class SampleCandidate:
    reference_filing_id: str
    issuer_group: str
    filing_year: int | None
    product_type: str
    trap_tags: list[str]


def stratified_sample(pool: list[SampleCandidate], *, target: int = 75, min_n: int = 50,
                      max_n: int = 100, seed: int = 7) -> list[SampleCandidate]:
    """At least one note per trap tag (when the pool has one), then round-robin
    over (issuer, year, product) strata until ``target``. Deterministic."""
    rng = random.Random(seed)
    items = sorted(pool, key=lambda c: c.reference_filing_id)
    rng.shuffle(items)
    chosen: dict[str, SampleCandidate] = {}
    for tag in TRAP_PATTERNS:
        for c in items:
            if tag in c.trap_tags and c.reference_filing_id not in chosen:
                chosen[c.reference_filing_id] = c
                break
    strata: dict[tuple, list[SampleCandidate]] = defaultdict(list)
    for c in items:
        strata[(c.issuer_group, c.filing_year, c.product_type)].append(c)
    keys = sorted(strata, key=lambda k: (str(k[0]), k[1] or 0, k[2]))
    goal = max(min_n, min(target, max_n))
    while len(chosen) < goal and any(strata[k] for k in keys):
        for k in keys:
            if strata[k] and len(chosen) < goal:
                c = strata[k].pop(0)
                chosen.setdefault(c.reference_filing_id, c)
    return list(chosen.values())[:max_n]


async def write_candidates(conn, sample: list[SampleCandidate], batch: str) -> int:
    from services.database import platform_scope

    async with platform_scope(conn):
        await conn.executemany(
            """INSERT INTO portfolio.note_gold_candidates
                 (reference_filing_id, sample_batch, issuer_group, filing_year, product_type, trap_tags)
               VALUES ($1, $2, $3, $4, $5, $6)
               ON CONFLICT (reference_filing_id) DO NOTHING""",
            [(c.reference_filing_id, batch, c.issuer_group, c.filing_year, c.product_type, c.trap_tags)
             for c in sample])
    return len(sample)
