"""Replication Studio (rep11) — STRUCTURED NOTES: replicability from the 424B2
terms, replicas built from rep8's options machinery, and the note-versus-replica
comparison (the embedded-fee ESTIMATE, only when every quote exists).

WHAT IS READ (public platform data, never written here)
──────────────────────────────────────────────────────────────────────────────
portfolio.securities_global_note_terms — the extracted payoff terms, one live
row per (note, terms status, filing). "The" current terms of a note: its live
rows ranked restated > final > preliminary, then the newest valid_from.
"Needs review" (never used to build): extraction_confidence other than 'high',
routing_decision 'queued', a used field whose field_status is
'extraction_failed', or a value outside the scale the field is defined in (a
cap or protection above 100% reads as the total payout, not the return; a
coupon above 1 reads as a percent). Preliminary terms are marked "preliminary —
final terms may differ" and may be built. Underlyings come from
portfolio.securities_global_relationships (underlying_of); building needs
exactly one RESOLVED edge (a Super Admin confirms it in the underlying queue).
Distribution agent and fees come from note_term_readings of the same filing
(status 'ok', a human reading first, then the newest) — shown when known.

THE VERDICT (platform; portfolio.note_replicability; the verdict job)
──────────────────────────────────────────────────────────────────────────────
not_replicable  any path or structure feature listed options cannot copy:
                autocall, worst-of, basket, decrement index, contingent coupon,
                memory coupon, a barrier observed daily, archetype 'other'.
                Every feature found is a reason (code + plain text).
static          buffered_note → 'buffer_package' (underlying + long put at 100%
                + short put at 100% − buffer + short call at the cap; a floor
                is a long put at the floor); principal_protected →
                'principal_protected' (Treasury zero + call spread at the
                participation and cap); reverse_convertible →
                'reverse_convertible' (Treasury paying principal + coupon +
                short put).
approximate     digital → 'digital_call_spread' (Treasury + a call spread of
                width ε approximating the digital: exact except underlying
                returns in [0, ε), where the replica pays less, by at most the
                digital amount); a total-return note (listed options settle on
                the price index).
A recompute writes a new row only when the terms fingerprint (the payoff
fields + field_status + confidence + the features read + RULE_VERSION)
changes; the earlier row is closed (valid_to). Idempotent.

PROTECTION SEMANTICS (stated, Joe to confirm on any surprise)
  protection_type 'buffer'  protection_pct = the first loss absorbed (0.20 = 20%)
  protection_type 'floor'   protection_pct = the minimum repaid, a fraction of
                            principal (1 = principal protected, 0.75 = never
                            below 75%) — consistent with the principal-protected
                            rows. A knock-in barrier labelled 'floor' would be
                            an extraction error and is flagged by the reader,
                            not guessed here.
  'none'                    one-for-one below the start level.
  cap_pct                   the maximum RETURN (0.237 = 23.7%); participation_rate
                            the upside multiple (absent = 1×); coupon_rate a
                            fraction a year (paid at maturity in the replica —
                            the timing difference is listed).

THE PAYOFF (pure, exact Decimal, per unit of principal; strikes as a fraction
of the start level s0; s = final / start)
    note value(s)    = 1 + note_return(spec, s − 1)
    replica value(s) = Σ underlying ratio·s + Σ Treasury face + Σ ±ratio·intrinsic(K, s)
At-expiry payoffs need strikes only and are always drawn. Premiums are not in
the payoff — they are the replica's COST (pricing).

PRICING (an ESTIMATE, per 100 of principal; every input listed with its as-of)
    underlying leg  ratio × current level ÷ start level × 100
    option leg      ± ratio × premium ÷ start level × 100   (a premium quote on
                    the contract; else its OWN entered implied vol through
                    Black–Scholes, no dividends; never a fitted surface)
    Treasury leg    face × 100 × (1 + y)^−T, y the Treasury yield quote, T =
                    days to the leg's maturity ÷ 365 from the yield's as-of
    embedded fee    note price (quote, % of par) − replica cost
Any input missing → "not priced — enter a quote", naming each. The SSVI surface
(services.pricing.ssvi_surface) is NOT used: it calibrates from call / put
price pairs (min_quotes_per_slice = 8 per expiry) and refuses maturities beyond
2.5 years — adviser quotes are one contract at a time, and notes run 1–6 years.

MEMBER VISIBILITY: the verdict and the stated terms are factual and visible to
the household's own members; replica cost and embedded-fee figures follow
rep10's hypothetical-performance policy (staff only without an approved policy
whose audience is staff_and_members).

Nothing here trades. Every leg is a manual instruction a person carries out.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_EVEN, Decimal, localcontext

import asyncpg

from services import human_tasks as HT
from services import options_sleeve as OS

log = logging.getLogger(__name__)

T_TERMS = "portfolio.securities_global_note_terms"
T_VERDICT = "portfolio.note_replicability"
T_REP = "public.note_replicas"
T_LEG = "public.note_replica_legs"
T_CMP = "public.note_replica_comparisons"
T_REL = "portfolio.securities_global_relationships"
T_READ = "portfolio.note_term_readings"

VIEW, MANAGE, APPROVE = OS.VIEW, OS.MANAGE, OS.APPROVE
SUBJECT = "note_replica"
TASK_APPROVAL = "note_replica_approval"
RULE_VERSION = "rep11.1"
SPREAD_SETTING = "replication.notes.digital_spread_pct"
STEP_SETTING = "replication.notes.payoff_step_pct"
DEFAULT_SPREAD = Decimal("0.01")
DEFAULT_STEP = Decimal("0.05")
GRID_LO, GRID_HI = Decimal("-1"), Decimal("1")
ZERO, ONE, HUNDRED = Decimal(0), Decimal(1), Decimal(100)
BP = Decimal("0.0001")
NOT_PRICED = OS.NOT_PRICED
ESTIMATE = "estimate"
PRELIMINARY = "preliminary — final terms may differ"
SSVI_MIN_PER_SLICE = 8          # services.pricing.ssvi_surface.SurfaceConfig.min_quotes_per_slice
SSVI_MAX_YEARS = Decimal("2.5")  # SurfaceConfig.max_maturity_years

VERDICT_LABELS = {"static": "Statically replicable", "approximate": "Approximately replicable",
                  "not_replicable": "Not replicable with listed options"}
MAPPING_LABELS = {
    "buffer_package": "Underlying + options (rep8's buffer package)",
    "principal_protected": "Treasury zero + call spread",
    "digital_call_spread": "Treasury + call spread approximating the digital",
    "reverse_convertible": "Treasury (principal + coupon) + short put",
}
STATUS_LABELS = {"draft": "Draft", "pending_approval": "Awaiting a second person's approval",
                 "approved": "Approved — the firm's replicable alternative", "archived": "Archived"}
LEG_KIND_LABELS = {"underlying": "Underlying", "option": "Option (listed / FLEX)", "treasury": "Treasury (zero / bill)"}
ARCHETYPE_LABELS = {"autocallable": "Autocallable", "buffered_note": "Buffered note", "digital": "Digital",
                    "principal_protected": "Principal protected", "reverse_convertible": "Reverse convertible",
                    "other": "Other"}
BASKET_LABELS = {"single": "Single underlying", "worst_of": "Worst-of", "basket": "Basket"}
PROTECTION_LABELS = {"buffer": "Buffer", "floor": "Floor (minimum repaid)", "none": "None"}
COMPARISON_STATUS_LABELS = {"priced": "Priced (estimate)", "not_priced": NOT_PRICED}
COUNSEL_ITEMS = (
    "Tax: the note (prepaid forward or contingent payment debt instrument) versus the replica (Section 1256 index "
    "options marked to market at 60/40; the straddle question pending with counsel since rep8) — for counsel, never "
    "decided in code.",
    "Whether showing an embedded-fee estimate to members is hypothetical performance / marketing — governed by the "
    "firm's hypothetical-performance policy (rep10); counsel confirms.",
    "The issuer-credit disclosure wording on the note page — counsel's item.",
)
READ_FIELDS = ("distribution", "agent_commission_pct", "structuring_fee_pct", "total_fees_pct", "estimated_value_pct",
               "price_to_public_pct", "coupon_memory", "barrier_observation", "issuer_entity", "maturity_date")
TERM_FIELDS = ("product_archetype", "protection_type", "basket_type", "return_basis", "is_decrement_index",
               "protection_pct", "cap_pct", "participation_rate", "coupon_rate", "coupon_barrier_pct",
               "autocall_barrier_pct", "autocall_frequency", "has_no_call_period", "no_call_months",
               "initial_valuation_date", "final_valuation_date", "tenor_years", "terms_status",
               "extraction_confidence", "routing_decision", "notional_currency")
STATUS_RANK = {"restated": 3, "final": 2, "preliminary": 1}
NUMBER_WORDS = {2: "two", 3: "three", 4: "four", 5: "five", 6: "six"}


class NoteError(ValueError):
    pass


class NoteNotFound(NoteError, LookupError):
    pass


class NoteConflict(NoteError):
    pass


class NoteRefused(NoteError):
    """The note cannot be built (not replicable, terms need review, underlying unconfirmed) — nothing written."""

    def __init__(self, message: str, reasons: list | None = None):
        super().__init__(message)
        self.reasons = reasons or []


class MakerCheckerViolation(PermissionError):
    pass


# ── small helpers ───────────────────────────────────────────────────────────
D, dstr = OS.D, OS.dstr


def _iso(v):
    return v.isoformat() if v is not None else None


def _uuid(v, what: str) -> str:
    try:
        return str(uuid.UUID(str(v)))
    except (TypeError, ValueError):
        raise NoteNotFound(f"{what} not found") from None


def _loads(v):
    if v is None or isinstance(v, (dict, list)):
        return v
    try:
        return json.loads(v)
    except (TypeError, ValueError):
        return None


def _dumps(v) -> str:
    return OS._dumps(v)


def pct_text(v: Decimal | None) -> str:
    if v is None:
        return "—"
    return f"{dstr(v * HUNDRED)}%"


async def _audit(conn, org_id, user_id, action: str, resource_id, payload: dict) -> None:
    await conn.execute(
        "INSERT INTO audit_log (org_id, user_id, action, resource_type, resource_id, payload) "
        "VALUES ($1::uuid, $2::uuid, $3, $4, $5::uuid, $6::jsonb)",
        str(org_id), str(user_id) if user_id else None, f"notes.{action}", SUBJECT, str(resource_id),
        _dumps(payload))


# ═══ Terms ═════════════════════════════════════════════════════════════════
_TERMS_SQL = f"""
SELECT t.*, g.name AS security_name, g.security_type,
       (SELECT i.id_value FROM portfolio.securities_global_identifiers i WHERE i.global_security_id = g.id
         AND i.id_type = 'cusip' AND i.valid_to IS NULL AND i.system_to IS NULL ORDER BY i.is_primary DESC LIMIT 1)
         AS cusip,
       f.filer_name, f.cik, f.form_type, f.filing_date, f.source_url, f.accession_number,
       (SELECT s.credit_entity FROM portfolio.structured_note_issuers s WHERE ltrim(s.filer_cik, '0') = ltrim(f.cik, '0')
         LIMIT 1) AS credit_entity,
       (SELECT s.issuer_group FROM portfolio.structured_note_issuers s WHERE ltrim(s.filer_cik, '0') = ltrim(f.cik, '0')
         LIMIT 1) AS issuer_group
FROM {T_TERMS} t
JOIN portfolio.securities_global g ON g.id = t.global_security_id
LEFT JOIN portfolio.reference_filings f ON f.id = t.reference_filing_id
WHERE t.valid_to IS NULL AND t.system_to IS NULL
"""


def current_of(rows: list[dict]) -> list[dict]:
    """One row per note: restated > final > preliminary, then the newest valid_from."""
    best: dict[str, dict] = {}
    for r in rows:
        k = str(r["global_security_id"])
        cur = best.get(k)
        key = (STATUS_RANK.get(r["terms_status"], 0), r["valid_from"])
        if cur is None or key > (STATUS_RANK.get(cur["terms_status"], 0), cur["valid_from"]):
            best[k] = r
    return list(best.values())


async def live_terms(conn, *, note_ids=None, terms_ids=None) -> list[dict]:
    sql, args = _TERMS_SQL, []
    if note_ids is not None:
        args.append([_uuid(x, "note") for x in note_ids])
        sql += f" AND t.global_security_id = ANY(${len(args)}::uuid[])"
    if terms_ids is not None:
        args.append([_uuid(x, "note terms") for x in terms_ids])
        sql += f" AND t.id = ANY(${len(args)}::uuid[])"
    rows = [dict(r) for r in await conn.fetch(sql + " ORDER BY g.name, t.id", *args)]
    for r in rows:
        r["field_status"] = _loads(r["field_status"]) or {}
    return rows


async def underlyings_of(conn, note_ids) -> dict[str, list[dict]]:
    ids = [str(x) for x in note_ids]
    out: dict[str, list[dict]] = {i: [] for i in ids}
    if not ids:
        return out
    rows = await conn.fetch(
        f"""SELECT r.from_global_security_id, r.to_global_security_id, r.proposed_global_security_id, r.link_state,
                   r.raw_underlying_text, r.proposal_kind, g.name AS target_name, g.security_type AS target_type,
                   p.name AS proposed_name
            FROM {T_REL} r
            LEFT JOIN portfolio.securities_global g ON g.id = r.to_global_security_id
            LEFT JOIN portfolio.securities_global p ON p.id = r.proposed_global_security_id
            WHERE r.from_global_security_id = ANY($1::uuid[]) AND r.relationship_type = 'underlying_of'
              AND r.valid_to IS NULL AND r.system_to IS NULL ORDER BY r.raw_underlying_text""", ids)
    for r in rows:
        out[str(r["from_global_security_id"])].append(dict(r))
    return out


async def readings_of(conn, filing_ids) -> dict[str, dict]:
    """field → value per filing (status 'ok'; a human reading first, then the newest)."""
    ids = [str(x) for x in filing_ids if x]
    out: dict[str, dict] = {}
    if not ids:
        return out
    rows = await conn.fetch(
        f"""SELECT DISTINCT ON (reference_filing_id, field_key) reference_filing_id, field_key, value, source,
                   created_at
            FROM {T_READ} WHERE reference_filing_id = ANY($1::uuid[]) AND field_key = ANY($2::text[])
              AND status = 'ok' AND value IS NOT NULL AND value::text <> 'null'
            ORDER BY reference_filing_id, field_key, (source = 'human') DESC, created_at DESC""", ids, list(READ_FIELDS))
    for r in rows:
        out.setdefault(str(r["reference_filing_id"]), {})[r["field_key"]] = {
            "value": _loads(r["value"]), "source": r["source"], "reviewed": r["source"] == "human"}
    return out


def underlying_state(edges: list[dict]) -> dict:
    """Building needs exactly one resolved underlying edge."""
    if not edges:
        return {"state": "none", "text": "no underlying recorded for this note", "security_id": None}
    resolved = [e for e in edges if e["link_state"] == "resolved"]
    if len(edges) == 1 and resolved:
        e = resolved[0]
        return {"state": "resolved", "text": f"{e['target_name']} (confirmed)", "security_id":
                str(e["to_global_security_id"]), "security_type": e["target_type"], "name": e["target_name"]}
    if len(edges) > 1:
        return {"state": "multiple", "text": f"{len(edges)} underlyings", "security_id": None}
    e = edges[0]
    proposed = f" — proposed: {e['proposed_name']}" if e.get("proposed_name") else ""
    return {"state": e["link_state"], "text": f"{e['raw_underlying_text']} — not confirmed ({e['link_state']}"
                                              f"{proposed}); a Super Admin confirms it in the underlying queue",
            "security_id": None}


# ═══ The verdict (pure) ════════════════════════════════════════════════════
def _reason(code: str, text: str, feature: str | None = None) -> dict:
    return {"code": code, "text": text, **({"feature": feature} if feature else {})}


def _reading(readings: dict, key: str):
    v = (readings or {}).get(key)
    return v.get("value") if isinstance(v, dict) else None


def assess(t: dict, *, n_underlyings: int | None = None, readings: dict | None = None) -> dict:
    """The verdict for ONE note-terms row. Pure: the reasons a member can read, the mapping, the payoff spec, and
    the review flags (preliminary is a mark, never a block)."""
    fs = t.get("field_status") or {}
    arch, basket, prot = t.get("product_archetype"), t.get("basket_type"), t.get("protection_type")
    block: list[dict] = []
    approx: list[dict] = []
    review: list[dict] = []
    freq = t.get("autocall_frequency")
    if arch == "autocallable" or (freq not in (None, "none")) or t.get("autocall_barrier_pct") is not None:
        when = f" ({freq})" if freq not in (None, "none") else ""
        block.append(_reason("autocall", f"Automatic early redemption (autocall) on observation dates{when} — the "
                             "note can end early depending on the path the underlying takes; a static set of "
                             "European options cannot stop on a call date.", "path"))
    if basket == "worst_of":
        n = NUMBER_WORDS.get(n_underlyings or 0, str(n_underlyings) if n_underlyings else "several")
        block.append(_reason("worst_of", f"Worst-of {n} underlyings — the payoff follows whichever falls most, so a "
                             "replica would need correlation, which listed options cannot provide.", "structure"))
    if basket == "basket":
        block.append(_reason("basket", "A weighted basket — the payoff is an option on the basket's combined level; "
                             "no listed option is written on that basket, and options on each component pay more "
                             "than one option on the sum.", "structure"))
    if t.get("is_decrement_index") is True:
        block.append(_reason("decrement_index", "A decrement index (a fixed amount deducted from the index every "
                             "day) — no listed or FLEX options are written on it.", "structure"))
    if t.get("coupon_barrier_pct") is not None:
        block.append(_reason("contingent_coupon", f"Contingent coupons, paid only when the underlying is at or above "
                             f"{pct_text(D(t['coupon_barrier_pct']))} of its start on each observation date — a strip "
                             "of digital options, one per date: path-dependent, not a static listed package.", "path"))
    if _reading(readings, "coupon_memory") is True:
        block.append(_reason("memory_coupon", "Memory coupons — a missed coupon is paid later if the underlying "
                             "recovers, which depends on the whole path.", "path"))
    obs = _reading(readings, "barrier_observation")
    if isinstance(obs, str) and obs.lower() in ("daily", "continuous", "intraday"):
        block.append(_reason("daily_barrier", f"The barrier is observed {obs.lower()} — a knock-in that depends on "
                             "the whole path; options that pay only at expiry cannot copy it.", "path"))
    if arch in (None, "other"):
        block.append(_reason("no_mapping", "The terms do not name a payoff shape this build maps to listed options "
                             f"(archetype {arch or 'not extracted'!r}).", "structure"))
    is_prelim = t.get("terms_status") == "preliminary"

    # review flags (never guessed past): confidence, routing, failed fields, out-of-scale values
    if t.get("extraction_confidence") != "high":
        review.append(_reason("confidence", f"Extraction confidence is {t.get('extraction_confidence') or 'unset'!r}"
                              " — a person reviews the terms before a replica is built."))
    if t.get("routing_decision") == "queued":
        review.append(_reason("queued", "The terms are queued for review (routing)."))
    used = ["product_archetype", "basket_type", "protection_type", "protection_pct", "cap_pct", "participation_rate",
            "initial_valuation_date", "final_valuation_date", "coupon_rate", "tenor_years"]
    failed = [k for k in used if fs.get(k) == "extraction_failed"]
    if failed:
        review.append(_reason("extraction_failed", f"Extraction failed for {', '.join(failed)}."))
    p, c, pp = D(t.get("participation_rate")), D(t.get("cap_pct")), D(t.get("protection_pct"))
    coupon = D(t.get("coupon_rate"))
    if c is not None and (c <= 0 or c > 1):
        review.append(_reason("cap_scale", f"The cap reads {dstr(c)} — above 100% it is probably the total payout, "
                              "not the return."))
    if p is not None and p <= 0:
        review.append(_reason("participation_scale", f"The participation rate reads {dstr(p)}."))
    if prot in ("buffer", "floor") and pp is None:
        review.append(_reason("protection_missing", f"A {prot} without its percentage."))
    if pp is not None and prot == "buffer" and not (ZERO < pp < ONE):
        review.append(_reason("protection_scale", f"The buffer reads {dstr(pp)} — outside 0 to 100%."))
    if pp is not None and prot == "floor" and not (ZERO < pp <= ONE):
        review.append(_reason("protection_scale", f"The floor reads {dstr(pp)} — outside 0 to 100% of principal."))
    if coupon is not None and coupon > 1:
        review.append(_reason("coupon_scale", f"The coupon reads {dstr(coupon)} — above 1 it is probably a percent, "
                              "not a fraction."))
    if not t.get("final_valuation_date") or not t.get("initial_valuation_date"):
        review.append(_reason("dates_missing", "The initial or final valuation date is missing — the options' "
                              "outcome period is unknown."))
    elif t["final_valuation_date"] <= t["initial_valuation_date"]:
        review.append(_reason("dates_order", "The final valuation date is not after the initial one."))

    if block:
        return {"verdict": "not_replicable", "mapping": None, "reasons": block, "spec": None,
                "is_preliminary": is_prelim, "needs_review": bool(review), "review_reasons": review}

    downside = {"kind": prot or "none"}
    if prot == "buffer":
        downside["buffer"] = pp
    elif prot == "floor":
        downside["floor"] = pp
    spec = {"downside": downside, "participation": p if p is not None else ONE, "cap": c}
    if arch == "buffered_note":
        mapping = "buffer_package"
        if coupon is not None:
            review.append(_reason("coupon_on_buffered", "A coupon on a buffered note — the extraction may have mixed "
                                  "archetypes."))
        reasons = [_reason("buffer_package", "Single underlying, paid at expiry: the underlying plus a long put at "
                           "100%, a short put where the buffer ends, extra calls for the participation and a short "
                           "call at the cap reproduce the payoff exactly (rep8's buffer package).")]
    elif arch == "principal_protected":
        mapping = "principal_protected"
        reasons = [_reason("principal_protected", "A Treasury zero repaying the protected principal plus a call "
                           "spread at the participation and cap reproduce the at-expiry payoff exactly.")]
    elif arch == "digital":
        mapping = "digital_call_spread"
        spec["digital"] = c
        spec["participation"] = ONE
        spec["cap"] = None
        if c is None:
            review.append(_reason("digital_missing", "No digital (fixed) return was extracted."))
        if p is not None and p != ONE:
            review.append(_reason("digital_participation", f"A participation rate ({dstr(p)}) on a digital note — "
                                  "the extraction may have mixed archetypes."))
        reasons = [_reason("digital", "A digital (fixed) return above the start level: a Treasury plus a narrow call "
                           "spread approximates the jump — exact everywhere except just above the start level, "
                           "where the replica pays less (stated with its tolerance).")]
        approx.append(reasons[0])
    elif arch == "reverse_convertible":
        mapping = "reverse_convertible"
        tenor = D(t.get("tenor_years"))
        if coupon is None or tenor is None:
            review.append(_reason("coupon_missing", "The coupon rate or the tenor is missing."))
        spec["coupon"] = (coupon * tenor) if (coupon is not None and tenor is not None) else None
        spec["participation"], spec["cap"] = None, None
        reasons = [_reason("reverse_convertible", "A Treasury repaying principal plus the coupon, and a short put at "
                           "the start level (or where the buffer ends), reproduce the at-expiry payoff exactly; the "
                           "coupon is paid at maturity in the replica (timing differs).")]
    else:  # pragma: no cover — archetypes are CHECK-constrained; 'other' / None blocked above
        return {"verdict": "not_replicable", "mapping": None,
                "reasons": [_reason("no_mapping", f"No mapping for archetype {arch!r}.", "structure")], "spec": None,
                "is_preliminary": is_prelim, "needs_review": bool(review), "review_reasons": review}
    if t.get("return_basis") == "total_return":
        r = _reason("total_return", "The note pays on a total-return index; listed and FLEX options settle on the "
                    "price index, so the replica differs by the dividends.")
        reasons.append(r)
        approx.append(r)
    spec["mapping"] = mapping
    return {"verdict": "approximate" if approx else "static", "mapping": mapping, "reasons": reasons,
            "spec": spec, "is_preliminary": is_prelim, "needs_review": bool(review), "review_reasons": review}


def spec_out(spec: dict | None) -> dict | None:
    if spec is None:
        return None

    def conv(v):
        if isinstance(v, Decimal):
            return dstr(v)
        if isinstance(v, dict):
            return {k: conv(x) for k, x in v.items()}
        return v
    return conv(spec)


def spec_in(spec: dict | None) -> dict | None:
    """The stored spec back to Decimals."""
    if spec is None:
        return None
    s = dict(spec)
    for k in ("participation", "cap", "digital", "coupon"):
        if s.get(k) is not None:
            s[k] = D(s[k])
    dn = dict(s.get("downside") or {"kind": "none"})
    for k in ("buffer", "floor"):
        if dn.get(k) is not None:
            dn[k] = D(dn[k])
    s["downside"] = dn
    return s


def fingerprint(t: dict, *, n_underlyings: int | None, readings: dict | None) -> str:
    payload = {k: (str(t.get(k)) if t.get(k) is not None else None) for k in TERM_FIELDS}
    payload["field_status"] = t.get("field_status") or {}
    payload["n_underlyings"] = n_underlyings
    payload["features"] = {k: _reading(readings, k) for k in ("coupon_memory", "barrier_observation")}
    payload["rule_version"] = RULE_VERSION
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def spec_complete(spec: dict | None) -> list[str]:
    """What the payoff still needs (empty = drawable)."""
    if spec is None:
        return ["a replicable mapping"]
    need = []
    dn = spec.get("downside") or {}
    if dn.get("kind") == "buffer" and dn.get("buffer") is None:
        need.append("the buffer")
    if dn.get("kind") == "floor" and dn.get("floor") is None:
        need.append("the floor")
    if spec["mapping"] == "digital_call_spread" and spec.get("digital") is None:
        need.append("the digital return")
    if spec["mapping"] == "reverse_convertible" and spec.get("coupon") is None:
        need.append("the coupon")
    return need


# ═══ The payoff (pure, exact) ══════════════════════════════════════════════
def _down(spec: dict, r: Decimal) -> Decimal:
    dn = spec.get("downside") or {"kind": "none"}
    if dn["kind"] == "buffer":
        b = dn["buffer"]
        return ZERO if r >= -b else r + b
    if dn["kind"] == "floor":
        return max(r, dn["floor"] - ONE)
    return r


def note_return(spec: dict, r: Decimal) -> Decimal:
    """The note's STATED at-expiry return for an underlying return r (per unit of principal)."""
    m = spec["mapping"]
    if m == "digital_call_spread":
        return spec["digital"] if r >= 0 else _down(spec, r)
    if m == "reverse_convertible":
        return spec["coupon"] + (ZERO if r >= 0 else _down(spec, r))
    if r >= 0:
        up = spec["participation"] * r
        return min(up, spec["cap"]) if spec.get("cap") is not None else up
    return _down(spec, r)


def _opt(option_type: str, direction: str, ratio: Decimal, strike_pct: Decimal, purpose: str) -> dict:
    return {"kind": "option", "option_type": option_type, "direction": direction, "ratio": ratio,
            "strike_pct": strike_pct, "purpose": purpose, "face_pct": None}


def legs_for(spec: dict, *, spread: Decimal) -> list[dict]:
    """The replica's legs from the payoff spec (strikes as a fraction of the start level)."""
    m = spec["mapping"]
    dn = spec.get("downside") or {"kind": "none"}
    legs: list[dict] = []
    if m == "buffer_package":
        legs.append({"kind": "underlying", "option_type": None, "direction": "long", "ratio": ONE, "strike_pct": None,
                     "purpose": "The underlying (one unit per unit of principal at the start level)",
                     "face_pct": None})
        if dn["kind"] == "buffer":
            legs.append(_opt("put", "long", ONE, ONE, "Long put at 100% — starts the buffer"))
            legs.append(_opt("put", "short", ONE, ONE - dn["buffer"], f"Short put at {pct_text(ONE - dn['buffer'])} "
                             "— ends the buffer"))
        elif dn["kind"] == "floor":
            legs.append(_opt("put", "long", ONE, dn["floor"], f"Long put at {pct_text(dn['floor'])} — the floor"))
        p = spec["participation"]
        if p > ONE:
            legs.append(_opt("call", "long", p - ONE, ONE, f"Long calls at 100% — participation {dstr(p)}×"))
        elif p < ONE:
            legs.append(_opt("call", "short", ONE - p, ONE, f"Short calls at 100% — participation {dstr(p)}×"))
        if spec.get("cap") is not None:
            k = ONE + spec["cap"] / p
            legs.append(_opt("call", "short", p, k, f"Short call at {pct_text(k)} — the cap"))
        return legs
    face = ONE + (spec.get("coupon") or ZERO) if m == "reverse_convertible" else ONE
    legs.append({"kind": "treasury", "option_type": None, "direction": "long", "ratio": ONE, "strike_pct": None,
                 "purpose": ("Treasury zero / bill maturing at the note's final valuation — repays principal"
                             + (" plus the coupon" if m == "reverse_convertible" else "")), "face_pct": face})
    if dn["kind"] == "none":
        legs.append(_opt("put", "short", ONE, ONE, "Short put at 100% — the one-for-one loss below the start"))
    elif dn["kind"] == "buffer":
        k = ONE - dn["buffer"]
        legs.append(_opt("put", "short", ONE, k, f"Short put at {pct_text(k)} — losses beyond the buffer"))
    elif dn["kind"] == "floor" and dn["floor"] < ONE:
        legs.append(_opt("put", "short", ONE, ONE, "Short put at 100% — losses below the start"))
        legs.append(_opt("put", "long", ONE, dn["floor"], f"Long put at {pct_text(dn['floor'])} — the floor"))
    if m == "principal_protected":
        p = spec["participation"]
        legs.append(_opt("call", "long", p, ONE, f"Long calls at 100% — participation {dstr(p)}×"))
        if spec.get("cap") is not None:
            k = ONE + spec["cap"] / p
            legs.append(_opt("call", "short", p, k, f"Short call at {pct_text(k)} — the cap"))
    elif m == "digital_call_spread":
        n = spec["digital"] / spread
        legs.append(_opt("call", "long", n, ONE, f"Long calls at 100% — the digital's call spread ({dstr(n)} per unit)"))
        legs.append(_opt("call", "short", n, ONE + spread, f"Short calls at {pct_text(ONE + spread)} — the call "
                         f"spread's upper strike (width {pct_text(spread)})"))
    return legs


def _intrinsic(option_type: str, k: Decimal, s: Decimal) -> Decimal:
    return OS.intrinsic(option_type, k, s)


def replica_value(legs: list[dict], s: Decimal) -> Decimal:
    """Per unit of principal at expiry. Underlying and option legs through rep8's package_value (same math);
    Treasury legs pay their face."""
    pl = [OS.PLeg("underlying" if x["kind"] == "underlying" else "option", x.get("option_type"), x["direction"],
                  D(x["ratio"]), D(x.get("strike_pct"))) for x in legs if x["kind"] != "treasury"]
    v = OS.package_value(pl, s)
    return v + sum((D(x["face_pct"]) * D(x["ratio"]) for x in legs if x["kind"] == "treasury"), ZERO)


def replica_return(legs: list[dict], r: Decimal) -> Decimal:
    return replica_value(legs, ONE + r) - ONE


def _strikes(legs) -> list[Decimal]:
    return sorted({D(x["strike_pct"]) for x in legs if x["kind"] == "option"})


def approximation(spec: dict, spread: Decimal | None) -> dict | None:
    """Where (and by how much at most) the replica differs from the note at expiry; None when exact."""
    if spec["mapping"] == "digital_call_spread":
        return {"region": [dstr(ZERO), dstr(spread)], "max_error": dstr(spec["digital"]),
                "tolerance_setting": SPREAD_SETTING,
                "text": (f"Exact at expiry except for underlying returns from 0% up to {pct_text(spread)} (the call "
                         f"spread's width): there the replica pays less than the note — {pct_text(spec['digital'])} "
                         "less at exactly 0%, falling in a straight line to nothing at "
                         f"{pct_text(spread)}. A narrower spread shrinks the region and costs more contracts.")}
    return None


def computed_terms(legs: list[dict], spec: dict) -> dict:
    """Cap / buffer / floor / participation read back FROM THE LEGS (exact), next to the note's stated values."""
    ks = _strikes(legs)
    top = (ks[-1] - ONE) if ks else ZERO
    cap = None
    a, b = replica_return(legs, max(top, ZERO) + ONE), replica_return(legs, max(top, ZERO) + Decimal(2))
    if a == b:
        cap = a
    base = replica_return(legs, ZERO)
    below = [k - ONE for k in ks if k < ONE]
    buffer = None
    if below:
        cand = max(below)
        edge = cand
        flat = replica_return(legs, edge) == base and all(replica_return(legs, k - ONE) == base for k in ks
                                                          if edge <= k - ONE <= ZERO)
        if flat and replica_return(legs, edge - Decimal("0.0001")) < base:
            buffer = -edge
    floor = replica_return(legs, -ONE)
    participation = None
    if spec["mapping"] in ("buffer_package", "principal_protected"):
        above = [k - ONE for k in ks if k > ONE]
        h = (min(above) / 2) if above else Decimal("0.5")
        participation = (replica_return(legs, h) - base) / h
    worst = min([replica_return(legs, k - ONE) for k in ks] + [floor, base])
    capm = spec["mapping"] in ("buffer_package", "principal_protected")
    stated = {"cap": spec.get("cap") if capm else None,
              "buffer": (spec.get("downside") or {}).get("buffer"),
              "floor": note_return(spec, -ONE),
              "participation": spec.get("participation") if capm else None}
    if spec["mapping"] == "digital_call_spread":
        stated["digital"] = spec["digital"]
    if spec["mapping"] == "reverse_convertible":
        stated["coupon"] = spec["coupon"]
    comp = {"cap": cap if capm else None, "buffer": buffer, "floor": floor, "participation": participation}
    if spec["mapping"] == "digital_call_spread":
        comp["digital"] = replica_return(legs, ONE)    # well above the spread
    if spec["mapping"] == "reverse_convertible":
        comp["coupon"] = base
    rows = []
    for k in stated:
        sv, cv = stated[k], comp.get(k)
        rows.append({"term": k, "stated": dstr(sv), "computed": dstr(cv),
                     "difference": dstr(cv - sv) if (sv is not None and cv is not None) else None,
                     "equal": sv == cv})
    return {"rows": rows, "worst_return": dstr(worst)}


def grid(step: Decimal, edges: list[Decimal]) -> list[Decimal]:
    pts, r = set(), GRID_LO
    while r <= GRID_HI:
        pts.add(r)
        r += step
    for e in edges:
        if e is not None and GRID_LO <= e <= GRID_HI:
            pts.add(e)
    return sorted(pts)


def payoff_points(spec: dict, legs: list[dict], *, step: Decimal) -> list[dict]:
    dn = spec.get("downside") or {}
    edges = [ZERO] + [k - ONE for k in _strikes(legs)]
    if dn.get("buffer") is not None:
        edges.append(-dn["buffer"])
    if dn.get("floor") is not None:
        edges.append(dn["floor"] - ONE)
    out = []
    for r in grid(step, edges):
        n, rep = note_return(spec, r), replica_return(legs, r)
        out.append({"underlying_return": dstr(r), "note_return": dstr(n), "replica_return": dstr(rep),
                    "difference": dstr(rep - n)})
    return out


def instruction_text(leg: dict, *, underlying: str, start_level: Decimal | None, contract: dict | None) -> str:
    """Every leg is a MANUAL instruction a person carries out (nothing here trades)."""
    if leg["kind"] == "underlying":
        return (f"Buy {dstr(D(leg['ratio']))} unit(s) of {underlying} per unit of principal at the start level — "
                "manual, through the custodian.")
    if leg["kind"] == "treasury":
        return (f"Buy a Treasury zero / bill with face {pct_text(D(leg['face_pct']))} of principal maturing "
                f"{_iso(leg['maturity'])} — manual, through the custodian's fixed-income desk.")
    level = (f" (level {dstr(D(leg['strike_level']))})" if leg.get("strike_level") is not None
             else " (level: not priced — enter the start level)")
    name = contract.get("name") if contract else f"{underlying} {leg['option_type']}"
    return (f"{'Buy' if leg['direction'] == 'long' else 'Sell (write)'} {dstr(D(leg['ratio']))} × {name}, strike "
            f"{pct_text(D(leg['strike_pct']))} of the start{level}, expiry {_iso(leg['expiry'])} per unit of "
            "principal — manual, through the custodian's options desk.")


# ═══ The verdict job (platform) ════════════════════════════════════════════
async def run_verdicts(pool, *, terms_ids=None) -> dict:
    """Compute the verdict for every live note-terms row (or the named ones). A new row only when the fingerprint
    changed; the earlier one is closed. Verdicts of terms that are no longer live are closed. Idempotent."""
    from services.replica_holdings import platform_tx

    async with pool.acquire() as conn:
        rows = await live_terms(conn, terms_ids=terms_ids)
        unds = await underlyings_of(conn, {r["global_security_id"] for r in rows})
        reads = await readings_of(conn, {r["reference_filing_id"] for r in rows})
    out = {"computed": 0, "unchanged": 0, "closed": 0, "counts": {k: 0 for k in VERDICT_LABELS}, "rows": len(rows)}
    async with platform_tx(pool) as conn:
        for t in rows:
            n = len(unds.get(str(t["global_security_id"]), []))
            rd = reads.get(str(t["reference_filing_id"])) if t["reference_filing_id"] else None
            a = assess(t, n_underlyings=n, readings=rd)
            fp = fingerprint(t, n_underlyings=n, readings=rd)
            out["counts"][a["verdict"]] += 1
            live = await conn.fetchrow(f"SELECT id, terms_fingerprint FROM {T_VERDICT} WHERE note_terms_id = $1 "
                                       "AND valid_to IS NULL AND system_to IS NULL FOR UPDATE", t["id"])
            if live and live["terms_fingerprint"] == fp:
                out["unchanged"] += 1
                continue
            if live:
                await conn.execute(f"UPDATE {T_VERDICT} SET valid_to = now() WHERE id = $1", live["id"])
                out["closed"] += 1
            await conn.execute(
                f"""INSERT INTO {T_VERDICT} (note_terms_id, global_security_id, terms_fingerprint, rule_version, verdict,
                        mapping, reasons, spec, is_preliminary, needs_review, review_reasons)
                    VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8::jsonb, $9, $10, $11::jsonb)""",
                t["id"], t["global_security_id"], fp, RULE_VERSION, a["verdict"], a["mapping"],
                _dumps(a["reasons"]), _dumps(spec_out(a["spec"])) if a["spec"] is not None else None,
                a["is_preliminary"], a["needs_review"], _dumps(a["review_reasons"]))
            out["computed"] += 1
        if terms_ids is None:
            gone = await conn.fetch(
                f"""UPDATE {T_VERDICT} v SET valid_to = now() WHERE v.valid_to IS NULL AND v.system_to IS NULL
                    AND NOT EXISTS (SELECT 1 FROM {T_TERMS} t WHERE t.id = v.note_terms_id AND t.valid_to IS NULL
                                    AND t.system_to IS NULL) RETURNING v.id""")
            out["closed"] += len(gone)
    return out


async def verdict_of(conn, note_terms_id) -> dict | None:
    row = await conn.fetchrow(f"SELECT * FROM {T_VERDICT} WHERE note_terms_id = $1::uuid AND valid_to IS NULL "
                              "AND system_to IS NULL", str(note_terms_id))
    return dict(row) if row else None


def verdict_out(v: dict | None) -> dict | None:
    if not v:
        return None
    return {"id": str(v["id"]), "verdict": v["verdict"], "verdict_label": VERDICT_LABELS[v["verdict"]],
            "mapping": v["mapping"], "mapping_label": MAPPING_LABELS.get(v["mapping"]) if v["mapping"] else None,
            "reasons": _loads(v["reasons"]) or [], "spec": _loads(v["spec"]), "is_preliminary": v["is_preliminary"],
            "preliminary_text": PRELIMINARY if v["is_preliminary"] else None, "needs_review": v["needs_review"],
            "review_reasons": _loads(v["review_reasons"]) or [], "rule_version": v["rule_version"],
            "computed_at": _iso(v["computed_at"]), "note_terms_id": str(v["note_terms_id"])}


# ═══ Reading: notes ════════════════════════════════════════════════════════
def _distribution(rd: dict | None) -> dict:
    """Distribution agent and fees as the filing's readings state them; "not in the extracted terms" otherwise."""
    rd = rd or {}
    out = {}
    for k in ("distribution", "agent_commission_pct", "structuring_fee_pct", "total_fees_pct", "estimated_value_pct",
              "price_to_public_pct"):
        v = rd.get(k)
        if v is not None:
            out[k] = {"value": v["value"], "reviewed": v["reviewed"],
                      "label": "read from the filing" + ("" if v["reviewed"] else " — a model reading, not reviewed")}
    return {"known": bool(out), "fields": out,
            "text": None if out else "not in the extracted terms — the filing's distribution and fees were not read"}


def terms_out(t: dict) -> dict:
    return {"note_terms_id": str(t["id"]), "global_security_id": str(t["global_security_id"]),
            "name": t["security_name"], "cusip": t.get("cusip"),
            "product_archetype": t["product_archetype"],
            "archetype_label": ARCHETYPE_LABELS.get(t["product_archetype"], t["product_archetype"]),
            "basket_type": t["basket_type"], "basket_label": BASKET_LABELS.get(t["basket_type"], t["basket_type"]),
            "protection_type": t["protection_type"],
            "protection_label": PROTECTION_LABELS.get(t["protection_type"], t["protection_type"]),
            "protection_pct": dstr(D(t["protection_pct"])), "cap_pct": dstr(D(t["cap_pct"])),
            "participation_rate": dstr(D(t["participation_rate"])), "coupon_rate": dstr(D(t["coupon_rate"])),
            "coupon_barrier_pct": dstr(D(t["coupon_barrier_pct"])),
            "autocall_barrier_pct": dstr(D(t["autocall_barrier_pct"])), "autocall_frequency": t["autocall_frequency"],
            "return_basis": t["return_basis"], "is_decrement_index": t["is_decrement_index"],
            "initial_valuation_date": _iso(t["initial_valuation_date"]),
            "final_valuation_date": _iso(t["final_valuation_date"]), "tenor_years": dstr(D(t["tenor_years"])),
            "terms_status": t["terms_status"], "extraction_confidence": t["extraction_confidence"],
            "routing_decision": t["routing_decision"], "field_status": t["field_status"],
            "issuer": t.get("filer_name"), "credit_entity": t.get("credit_entity") or t.get("filer_name"),
            "issuer_group": t.get("issuer_group"),
            "filing": {"form_type": t.get("form_type"), "filing_date": _iso(t.get("filing_date")),
                       "url": t.get("source_url"), "accession_number": t.get("accession_number")}}


async def list_notes(conn, *, verdict: str | None = None, archetype: str | None = None, issuer: str | None = None,
                     underlying: str | None = None) -> list[dict]:
    rows = current_of(await live_terms(conn))
    unds = await underlyings_of(conn, {r["global_security_id"] for r in rows})
    out = []
    for t in rows:
        v = verdict_out(await verdict_of(conn, t["id"]))
        edges = unds.get(str(t["global_security_id"]), [])
        und_text = ", ".join(e["target_name"] or e["proposed_name"] or e["raw_underlying_text"] for e in edges) or "—"
        rec = {**terms_out(t), "verdict": v, "underlyings": und_text,
               "underlying_state": underlying_state(edges)["state"]}
        if verdict and (v or {}).get("verdict") != verdict:
            continue
        if archetype and t["product_archetype"] != archetype:
            continue
        if issuer and issuer.lower() not in (f"{t.get('filer_name') or ''} {t.get('issuer_group') or ''} "
                                             f"{t.get('credit_entity') or ''}").lower():
            continue
        if underlying and underlying.lower() not in und_text.lower():
            continue
        out.append(rec)
    return out


async def load_current_terms(conn, note_id) -> dict:
    rows = current_of(await live_terms(conn, note_ids=[note_id]))
    if not rows:
        raise NoteNotFound("no extracted terms for this note")
    return rows[0]


async def note_detail(conn, org_id, note_id, *, step: Decimal | None = None) -> dict:
    t = await load_current_terms(conn, note_id)
    v = await verdict_of(conn, t["id"])
    edges = (await underlyings_of(conn, [t["global_security_id"]]))[str(t["global_security_id"])]
    rd = (await readings_of(conn, [t["reference_filing_id"]])).get(str(t["reference_filing_id"])) \
        if t["reference_filing_id"] else None
    spread = await spread_of(conn, org_id)
    st = step if step is not None else await OS._setting(conn, org_id, STEP_SETTING, DEFAULT_STEP,
                                                         lo=Decimal("0.005"), hi=Decimal("0.25"))
    payoff = None
    if v is not None and v["verdict"] != "not_replicable":
        spec = spec_in(_loads(v["spec"]))
        need = spec_complete(spec)
        if need:
            payoff = {"drawn": False, "text": f"payoff not drawn — the terms lack {', '.join(need)}"}
        else:
            legs = legs_for(spec, spread=spread)
            payoff = {"drawn": True, "step": dstr(st), "points": payoff_points(spec, legs, step=st),
                      "legs": [_leg_brief(x) for x in legs], "computed": computed_terms(legs, spec),
                      "approximation": approximation(spec, spread),
                      "note": "at expiry, from strikes only (no prices needed); premiums are the replica's cost, "
                              "not its payoff"}
    replicas = [replica_out(dict(r)) for r in await conn.fetch(
        f"SELECT * FROM {T_REP} WHERE org_id = $1::uuid AND global_security_id = $2::uuid AND system_to IS NULL "
        "ORDER BY created_at DESC", str(org_id), str(t["global_security_id"]))]
    return {"note": terms_out(t), "verdict": verdict_out(v), "underlying": underlying_state(edges),
            "underlyings": [{"text": e["raw_underlying_text"], "state": e["link_state"],
                             "target": e["target_name"], "proposed": e["proposed_name"]} for e in edges],
            "distribution": _distribution(rd), "payoff": payoff, "replicas": replicas,
            "differences": differences(t, (v or {}).get("mapping"), approx=(payoff or {}).get("approximation"),
                                       distribution=_distribution(rd)),
            "buildable": buildable(v, edges)}


def buildable(v: dict | None, edges: list[dict]) -> dict:
    why = []
    if v is None:
        why.append("no verdict yet — the verdict job has not run over these terms")
    else:
        if v["verdict"] == "not_replicable":
            why.extend(r["text"] for r in (_loads(v["reasons"]) or []))
        if v["needs_review"]:
            why.extend(f"Terms need review: {r['text']}" for r in (_loads(v["review_reasons"]) or []))
        if v["verdict"] != "not_replicable" and spec_complete(spec_in(_loads(v["spec"]))):
            why.append(f"The terms lack {', '.join(spec_complete(spec_in(_loads(v['spec']))))}.")
    us = underlying_state(edges)
    if us["state"] != "resolved":
        why.append(f"Underlying: {us['text']}.")
    return {"ok": not why, "why": why}


def differences(t: dict, mapping: str | None, *, approx: dict | None = None, distribution: dict | None = None
                ) -> list[dict]:
    """The differences that matter between the note and a replica (factual; counsel items marked)."""
    issuer = t.get("credit_entity") or t.get("filer_name") or "the issuer"
    out = [
        {"key": "credit", "label": "Issuer credit",
         "text": f"The note is an unsecured promise of {issuer}: if the issuer cannot pay, the holder can lose "
                 "principal whatever the index does. The replica holds exchange-cleared options (OCC) and Treasuries. "
                 "Disclosure wording is counsel's item."},
        {"key": "liquidity", "label": "Liquidity",
         "text": "A note is usually bought back only by the issuer's dealer, at a price it sets. Listed options and "
                 "Treasuries can be closed any trading day; FLEX options trade by request and their bid / ask can be "
                 "wide."},
        {"key": "tax", "label": "Tax (counsel)",
         "text": "The note is typically taxed as a prepaid forward or a contingent payment debt instrument; broad-"
                 "based index options are Section 1256 contracts (60/40, marked to market each year), and the "
                 "straddle question is pending with counsel (rep8). Not decided here."},
        {"key": "path", "label": "Between now and expiry",
         "text": "The replica copies the payoff at expiry only. Before then its value moves with the options' "
                 "prices, and the note's with the issuer's bid — the two can differ a great deal."},
    ]
    if mapping == "buffer_package" and t.get("return_basis") != "total_return":
        out.append({"key": "dividends", "label": "Dividends",
                    "text": "The note pays on the price index; a replica holding the underlying also receives its "
                            "dividends."})
    if mapping == "reverse_convertible":
        out.append({"key": "coupon_timing", "label": "Coupon timing",
                    "text": "The note pays its coupon on its schedule; the replica's Treasury pays it at maturity."})
    if approx:
        out.append({"key": "approximation", "label": "Approximation", "text": approx["text"]})
    if distribution and distribution.get("known"):
        out.append({"key": "distribution", "label": "Distribution and fees",
                    "text": "The filing states a distribution agent and fees (shown with the terms)."})
    return out


def _leg_brief(x: dict) -> dict:
    return {"kind": x["kind"], "kind_label": LEG_KIND_LABELS[x["kind"]], "option_type": x.get("option_type"),
            "direction": x["direction"], "ratio": dstr(D(x["ratio"])), "strike_pct": dstr(D(x.get("strike_pct"))),
            "face_pct": dstr(D(x.get("face_pct"))), "purpose": x["purpose"]}


async def spread_of(conn, org_id) -> Decimal:
    return await OS._setting(conn, org_id, SPREAD_SETTING, DEFAULT_SPREAD, lo=Decimal("0.001"), hi=Decimal("0.10"))


# ═══ Replicas ══════════════════════════════════════════════════════════════
def replica_out(r: dict) -> dict:
    return {"id": str(r["id"]), "global_security_id": str(r["global_security_id"]),
            "note_terms_id": str(r["note_terms_id"]), "replicability_id": str(r["replicability_id"]),
            "verdict": r["verdict"], "verdict_label": VERDICT_LABELS[r["verdict"]], "mapping": r["mapping"],
            "mapping_label": MAPPING_LABELS[r["mapping"]],
            "underlying_global_security_id": str(r["underlying_global_security_id"]),
            "outcome_start": _iso(r["outcome_start"]), "outcome_end": _iso(r["outcome_end"]),
            "start_level": dstr(D(r["start_level"])),
            "start_level_text": None if r["start_level"] is not None else
            f"{NOT_PRICED}: the underlying's level on {_iso(r['outcome_start'])}",
            "start_level_quote_id": str(r["start_level_quote_id"]) if r["start_level_quote_id"] else None,
            "treasury_face_pct": dstr(D(r["treasury_face_pct"])), "treasury_maturity": _iso(r["treasury_maturity"]),
            "treasury_global_security_id": str(r["treasury_global_security_id"]) if r["treasury_global_security_id"]
            else None, "broad_based_index": r["broad_based_index"], "exercise_style": r["exercise_style"],
            "settlement": r["settlement"], "is_flex": r["is_flex"], "multiplier": dstr(D(r["multiplier"])),
            "digital_spread_pct": dstr(D(r["digital_spread_pct"])), "computed": _loads(r["computed"]) or {},
            "approximation": _loads(r["approximation"]), "status": r["status"],
            "status_label": STATUS_LABELS[r["status"]], "maker_ids": [str(m) for m in r["maker_ids"] or []],
            "checker_id": str(r["checker_id"]) if r["checker_id"] else None,
            "approval_task_id": str(r["approval_task_id"]) if r["approval_task_id"] else None,
            "decided_at": _iso(r["decided_at"]), "decision_note": r["decision_note"],
            "status_reason": r["status_reason"], "created_at": _iso(r["created_at"])}


async def load_replica(conn, org_id, replica_id, *, lock: bool = False) -> dict:
    row = await conn.fetchrow(f"SELECT * FROM {T_REP} WHERE id = $1::uuid AND org_id = $2::uuid AND system_to IS NULL"
                              + (" FOR UPDATE" if lock else ""), _uuid(replica_id, "replica"), str(org_id))
    if row is None:
        raise NoteNotFound("replica not found")
    return dict(row)


async def legs_of(conn, replica_id) -> list[dict]:
    return [dict(r) for r in await conn.fetch(
        f"SELECT * FROM {T_LEG} WHERE replica_id = $1::uuid AND valid_to IS NULL AND system_to IS NULL ORDER BY leg_no",
        str(replica_id))]


async def _archive(conn, replica_id) -> None:
    got = await conn.fetchval(
        f"""INSERT INTO {T_REP}
            SELECT (jsonb_populate_record(NULL::{T_REP}, to_jsonb(t) || jsonb_build_object(
                'id', gen_random_uuid(), 'archived_from_id', t.id, 'system_to', now()))).*
            FROM {T_REP} t WHERE t.id = $1::uuid AND t.system_to IS NULL RETURNING id""", str(replica_id))
    if got is None:
        raise NoteConflict("the replica is not live — refusing to change it")


async def _update(conn, replica_id, fields: dict) -> dict:
    await _archive(conn, replica_id)
    cols = list(fields)
    sets = ", ".join(f"{c} = ${i + 2}" for i, c in enumerate(cols))
    row = await conn.fetchrow(f"UPDATE {T_REP} SET {sets}, updated_at = now(), system_from = now() WHERE id = $1::uuid "
                              "AND system_to IS NULL RETURNING *", str(replica_id), *fields.values())
    if row is None:
        raise NoteConflict("the replica changed underneath this request — reload and retry")
    return dict(row)


async def _write_legs(conn, org_id, replica_id, legs: list[dict], actor_id) -> None:
    await conn.execute(f"UPDATE {T_LEG} SET valid_to = now() WHERE replica_id = $1::uuid AND valid_to IS NULL "
                       "AND system_to IS NULL", str(replica_id))
    for i, x in enumerate(legs, 1):
        await conn.execute(
            f"""INSERT INTO {T_LEG} (org_id, replica_id, leg_no, kind, purpose, option_type, direction, ratio,
                    strike_pct, strike_level, expiry, contract_id, face_pct, maturity, created_by)
                VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12::uuid, $13, $14, $15::uuid)""",
            str(org_id), str(replica_id), i, x["kind"], x["purpose"], x.get("option_type"), x["direction"],
            D(x["ratio"]), D(x.get("strike_pct")), D(x.get("strike_level")), x.get("expiry"),
            str(x["contract_id"]) if x.get("contract_id") else None, D(x.get("face_pct")), x.get("maturity"),
            str(actor_id) if actor_id else None)


async def _start_level(conn, org_id, *, quote_id, underlying: dict, on: date) -> dict | None:
    """The start level: the named quote (the underlying's level, as of the initial valuation date), else the
    newest such quote on that date; None when there is none (strikes stay as percentages)."""
    want = OS.level_kind(underlying["security_type"])
    if quote_id:
        q = await conn.fetchrow(f"SELECT * FROM {OS.T_QUOTE} WHERE id = $1::uuid AND org_id = $2::uuid "
                                "AND valid_to IS NULL AND system_to IS NULL", _uuid(quote_id, "quote"), str(org_id))
        if q is None:
            raise NoteNotFound("quote not found")
        if str(q["global_security_id"]) != str(underlying["id"]) or q["kind"] != want:
            raise NoteError(f"the start level is a {OS.QUOTE_KIND_LABELS[want].lower()} quote on the note's underlying")
        if q["as_of"].astimezone(timezone.utc).date() != on:
            raise NoteError(f"the start level is the underlying's level on the initial valuation date {on.isoformat()}"
                            f"; this quote is as of {q['as_of'].astimezone(timezone.utc).date().isoformat()}")
        return dict(q)
    return await OS.latest_quote(conn, org_id, underlying["id"], want, on_date=on)


async def _contracts(pool, *, actor_id, underlying_id, legs: list[dict], start_level: Decimal, broad_based: bool,
                     style: str, settlement: str, is_flex: bool, multiplier: Decimal) -> list[dict]:
    out = []
    for x in legs:
        x = dict(x)
        if x["kind"] == "option":
            level = (D(x["strike_pct"]) * start_level).quantize(OS.CENT, rounding=ROUND_HALF_EVEN)
            c = await OS.find_or_create_contract(
                pool, actor_id=actor_id, underlying_global_security_id=underlying_id, option_type=x["option_type"],
                strike=level, expiry=x["expiry"], exercise_style=style, settlement=settlement, is_flex=is_flex,
                multiplier=multiplier, broad_based_index=broad_based)
            x["strike_level"], x["contract_id"] = level, c["id"]
        out.append(x)
    return out


async def build(pool, *, org_id, actor_id, note_id, broad_based_index: bool | None = None, is_flex: bool = True,
                exercise_style: str = "european", settlement: str | None = None, multiplier="100",
                start_level_quote_id=None, treasury_global_security_id=None) -> dict:
    """A DRAFT replica of a static / approximate note. Refused — with the reasons, NOTHING written — for a
    not-replicable note, terms that need review, or an unconfirmed underlying."""
    async with pool.acquire() as conn:
        actor = await HT.assert_human(conn, actor_id, org_id)
    return await _build(pool, org_id=org_id, actor_id=actor["id"], note_id=note_id,
                        broad_based_index=broad_based_index, is_flex=is_flex, exercise_style=exercise_style,
                        settlement=settlement, multiplier=multiplier, start_level_quote_id=start_level_quote_id,
                        treasury_global_security_id=treasury_global_security_id)


async def prepare_draft(pool, *, org_id, note_id, broad_based_index: bool) -> dict:
    """rep12 — the AUTOMATIC check's DRAFT replica: the same builder, with NO person (created_by NULL, no maker).
    Same refusals as :func:`build` (NoteRefused with the reasons, nothing written). The draft is never submitted
    or approved here: a person sets what is missing, submits it, and a second person approves (maker-checker)."""
    return await _build(pool, org_id=org_id, actor_id=None, note_id=note_id, broad_based_index=broad_based_index,
                        is_flex=True, exercise_style="european", settlement=None, multiplier="100",
                        start_level_quote_id=None, treasury_global_security_id=None, source="automatic_check")


async def _build(pool, *, org_id, actor_id, note_id, broad_based_index, is_flex, exercise_style, settlement,
                 multiplier, start_level_quote_id, treasury_global_security_id, source: str = "person") -> dict:
    """The builder. ``actor_id`` is an admitted person (asserted by :func:`build`) or None for the automatic check
    (:func:`prepare_draft`) — then the draft has no maker and no creator."""
    if exercise_style not in OS.EXERCISE_STYLES:
        raise NoteError(f"exercise style is one of {list(OS.EXERCISE_STYLES)}")
    mult = OS._need_decimal(multiplier, "multiplier")
    async with pool.acquire() as conn:
        t = await load_current_terms(conn, note_id)
    v = None
    async with pool.acquire() as conn:
        v = await verdict_of(conn, t["id"])
    if v is None:                     # the job has not reached these terms yet: compute them now (platform job)
        await run_verdicts(pool, terms_ids=[t["id"]])
        async with pool.acquire() as conn:
            v = await verdict_of(conn, t["id"])
    async with pool.acquire() as conn:
        edges = (await underlyings_of(conn, [t["global_security_id"]]))[str(t["global_security_id"])]
        b = buildable(v, edges)
        if not b["ok"]:
            raise NoteRefused("this note cannot be replicated here: " + " ".join(b["why"]), b["why"])
        und_state = underlying_state(edges)
        und = await OS._security(conn, und_state["security_id"])
        if await conn.fetchval(f"SELECT 1 FROM {T_REP} WHERE org_id = $1::uuid AND global_security_id = $2::uuid "
                               "AND status IN ('draft', 'pending_approval') AND system_to IS NULL", str(org_id),
                               str(t["global_security_id"])):
            raise NoteConflict("an open replica of this note already exists — work on it (or archive it) first")
        kind = OS.underlying_kind(und["security_type"])
        settle = settlement or ("cash" if kind == "index" else "physical")
        if settle not in OS.SETTLEMENTS:
            raise NoteError(f"settlement is one of {list(OS.SETTLEMENTS)}")
        if kind != "index" and settle == "cash":
            raise NoteError("only index options settle in cash here; a stock or fund option settles physically")
        if kind == "index" and broad_based_index is None:
            raise NoteError("state whether the underlying index is broad-based (it decides Section 1256)")
        broad = bool(broad_based_index) if kind == "index" else False
        spec = spec_in(_loads(v["spec"]))
        spread = await spread_of(conn, org_id)
        tsy = None
        if treasury_global_security_id:
            tsy = await OS._security(conn, treasury_global_security_id)
            if tsy["security_type"] != "fixed_income":
                raise NoteError("the Treasury leg's security is a fixed-income security")
        q = await _start_level(conn, org_id, quote_id=start_level_quote_id, underlying=und,
                               on=t["initial_valuation_date"])
    legs = legs_for(spec, spread=spread)
    end = t["final_valuation_date"]
    for x in legs:
        if x["kind"] == "option":
            x["expiry"] = end
        if x["kind"] == "treasury":
            x["maturity"] = end
    level = D(q["value"]) if q else None
    if level is not None:
        legs = await _contracts(pool, actor_id=actor_id, underlying_id=und["id"], legs=legs, start_level=level,
                                broad_based=broad, style=exercise_style, settlement=settle, is_flex=is_flex,
                                multiplier=mult)
    comp = computed_terms(legs, spec)
    approx = approximation(spec, spread)
    tsy_leg = next((x for x in legs if x["kind"] == "treasury"), None)
    async with pool.acquire() as conn:
        async with conn.transaction():
            try:
                row = await conn.fetchrow(
                    f"""INSERT INTO {T_REP} (org_id, global_security_id, note_terms_id, replicability_id, verdict, mapping,
                            spec, underlying_global_security_id, outcome_start, outcome_end, start_level,
                            start_level_quote_id, treasury_face_pct, treasury_maturity, treasury_global_security_id,
                            broad_based_index, exercise_style, settlement, is_flex, multiplier, digital_spread_pct,
                            computed, approximation, status, maker_ids, created_by)
                        VALUES ($1::uuid, $2::uuid, $3::uuid, $4::uuid, $5, $6, $7::jsonb, $8::uuid, $9, $10, $11,
                                $12::uuid, $13, $14, $15::uuid, $16, $17, $18, $19, $20, $21, $22::jsonb, $23::jsonb,
                                'draft', $24::uuid[], $25::uuid) RETURNING *""",
                    str(org_id), str(t["global_security_id"]), str(t["id"]), str(v["id"]), v["verdict"], v["mapping"],
                    _dumps(spec_out(spec)), str(und["id"]), t["initial_valuation_date"], end, level,
                    str(q["id"]) if q else None, D(tsy_leg["face_pct"]) if tsy_leg else None,
                    end if tsy_leg else None, str(tsy["id"]) if tsy else None, broad, exercise_style, settle, is_flex,
                    mult, spread if v["mapping"] == "digital_call_spread" else None, _dumps(comp),
                    _dumps(approx) if approx else None, [str(actor_id)] if actor_id else [],
                    str(actor_id) if actor_id else None)
            except asyncpg.UniqueViolationError:
                raise NoteConflict("an open replica of this note already exists") from None
            await _write_legs(conn, org_id, row["id"], legs, actor_id)
            await _audit(conn, org_id, actor_id, "replica_built", row["id"],
                         {"note": str(t["global_security_id"]), "verdict": v["verdict"], "mapping": v["mapping"],
                          "legs": len(legs), "start_level": dstr(level), "source": source})
    async with pool.acquire() as conn:
        return await replica_detail(conn, org_id, row["id"])


async def _price_draft_levels(pool, *, org_id, actor_id, r: dict, start_level_quote_id=None,
                              treasury_global_security_id=None) -> dict:
    """On a DRAFT: set the start level (and strike the contracts) when it was missing, and the Treasury security."""
    async with pool.acquire() as conn:
        und = await OS._security(conn, r["underlying_global_security_id"])
        fields = {}
        q = None
        if r["start_level"] is None or start_level_quote_id:
            q = await _start_level(conn, org_id, quote_id=start_level_quote_id, underlying=und, on=r["outcome_start"])
        if treasury_global_security_id:
            tsy = await OS._security(conn, treasury_global_security_id)
            if tsy["security_type"] != "fixed_income":
                raise NoteError("the Treasury leg's security is a fixed-income security")
            if r["mapping"] == "buffer_package":
                raise NoteError("this replica has no Treasury leg")
            fields["treasury_global_security_id"] = str(tsy["id"])
        legs = await legs_of(conn, r["id"])
    if q is not None:
        level = D(q["value"])
        new = await _contracts(pool, actor_id=actor_id, underlying_id=und["id"],
                               legs=[{**x, "strike_pct": D(x["strike_pct"]), "ratio": D(x["ratio"])} for x in legs],
                               start_level=level, broad_based=r["broad_based_index"], style=r["exercise_style"],
                               settlement=r["settlement"], is_flex=r["is_flex"], multiplier=D(r["multiplier"]))
        fields["start_level"], fields["start_level_quote_id"] = level, str(q["id"])
    else:
        new = None
    if not fields:
        return r
    makers = [str(m) for m in r["maker_ids"] or []]
    if str(actor_id) not in makers:
        makers.append(str(actor_id))
    fields["maker_ids"] = makers
    async with pool.acquire() as conn:
        async with conn.transaction():
            cur = await load_replica(conn, org_id, r["id"], lock=True)
            if cur["status"] != "draft":
                raise NoteConflict(f"the replica is {cur['status']}; only a draft is changed")
            r = await _update(conn, r["id"], fields)
            if new is not None:
                await _write_legs(conn, org_id, r["id"], new, actor_id)
            await _audit(conn, org_id, actor_id, "replica_levels_set", r["id"],
                         {k: str(v) for k, v in fields.items() if k != "maker_ids"})
    return r


# ── pricing (an ESTIMATE) ──────────────────────────────────────────────────
_PI = Decimal("3.14159265358979323846264338327950288419716939937510")


def _erf(x: Decimal) -> Decimal:
    """erf by its Maclaurin series (exact Decimal arithmetic at the context precision; |x| ≤ 6, else ±1)."""
    if x > 6:
        return ONE
    if x < -6:
        return -ONE
    term, total, n = x, x, 0
    while True:
        n += 1
        term = -term * x * x / n
        add = term / (2 * n + 1)
        if abs(add) < Decimal("1e-40"):
            break
        total += add
    return total * 2 / _PI.sqrt()


def _ncdf(x: Decimal) -> Decimal:
    return (ONE + _erf(x / Decimal(2).sqrt())) / 2


def black_scholes(option_type: str, s: Decimal, k: Decimal, t_years: Decimal, rate: Decimal, vol: Decimal) -> Decimal:
    """European price, no dividends, continuously compounded rate; exact Decimal at 50 digits."""
    with localcontext() as ctx:
        ctx.prec = 50
        if t_years <= 0 or vol <= 0:
            return OS.intrinsic(option_type, k, s)
        sq = vol * t_years.sqrt()
        d1 = ((s / k).ln() + (rate + vol * vol / 2) * t_years) / sq
        d2 = d1 - sq
        disc = (-rate * t_years).exp()
        if option_type == "call":
            return s * _ncdf(d1) - k * disc * _ncdf(d2)
        return k * disc * _ncdf(-d2) - s * _ncdf(-d1)


def discount(face: Decimal, y_pct: Decimal, days: int) -> Decimal:
    """face × (1 + y)^−T, y in percent a year (annual compounding), T = days ÷ 365."""
    with localcontext() as ctx:
        ctx.prec = 28
        return face * (ONE + y_pct / HUNDRED) ** (-Decimal(days) / Decimal(365))


def _q(q: dict, label: str) -> dict:
    return {"label": label, "quote_id": str(q["id"]), "kind": q["kind"], "value": dstr(D(q["value"])),
            "source": q["source"], "as_of": q["as_of"].isoformat()}


async def compute_cost(conn, org_id, r: dict, legs: list[dict]) -> dict:
    """The replica's cost and the embedded-fee estimate per 100 of principal. Pure reads; every input listed."""
    inputs, missing = [], []
    note = await OS._security(conn, r["global_security_id"])
    und = await OS._security(conn, r["underlying_global_security_id"])
    und_label = und["ticker"] or und["name"]
    nq = await OS.latest_quote(conn, org_id, r["global_security_id"], "note_price")
    if nq:
        inputs.append(_q(nq, f"Note price — {note['name']}"))
    else:
        missing.append(f"the note's price (a note_price quote on {note['name']}, % of par)")
    l0 = D(r["start_level"])
    if l0 is None:
        missing.append(f"the start level ({und_label}'s level on {_iso(r['outcome_start'])})")
    else:
        inputs.append({"label": f"Start level — {und_label} on {_iso(r['outcome_start'])}", "value": dstr(l0),
                       "quote_id": str(r["start_level_quote_id"]) if r["start_level_quote_id"] else None})
    lk = OS.level_kind(und["security_type"])
    ln = await OS.latest_quote(conn, org_id, und["id"], lk)
    tsy_y = None
    if r["mapping"] != "buffer_package":
        if r["treasury_global_security_id"] is None:
            missing.append("the Treasury leg's security (choose a Treasury maturing near "
                           f"{_iso(r['treasury_maturity'])}) and its yield")
        else:
            tsy = await OS._security(conn, r["treasury_global_security_id"])
            tsy_y = await OS.latest_quote(conn, org_id, tsy["id"], "treasury_yield")
            if tsy_y:
                inputs.append(_q(tsy_y, f"Treasury yield — {tsy['name']}"))
            else:
                missing.append(f"the Treasury yield (a treasury_yield quote on {tsy['name']})")
    total = ZERO
    for x in legs:
        if x["kind"] == "underlying":
            if ln is None:
                missing.append(f"{und_label}'s current level (an {lk} quote)")
                continue
            inputs.append(_q(ln, f"Current level — {und_label}"))
            if l0 is not None:
                total += D(x["ratio"]) * D(ln["value"]) / l0
        elif x["kind"] == "treasury":
            if tsy_y is not None:
                days = max(0, (x["maturity"] - tsy_y["as_of"].astimezone(timezone.utc).date()).days)
                total += discount(D(x["face_pct"]) * D(x["ratio"]), D(tsy_y["value"]), days)
        else:
            if x["contract_id"] is None:
                missing.append(f"the {x['option_type']} at {pct_text(D(x['strike_pct']))}'s contract (set the start "
                               "level first)")
                continue
            c = await OS.load_contract(conn, contract_id=x["contract_id"])
            sign = ONE if x["direction"] == "long" else -ONE
            pq = await OS.latest_quote(conn, org_id, c["global_security_id"], "premium")
            if pq:
                inputs.append(_q(pq, f"Premium — {c['name']}"))
                if l0 is not None:
                    total += sign * D(x["ratio"]) * D(pq["value"]) / l0
                continue
            iv = await OS.latest_quote(conn, org_id, c["global_security_id"], "implied_vol")
            if iv and ln is not None and tsy_y is not None and l0 is not None:
                asof = iv["as_of"].astimezone(timezone.utc).date()
                ty = Decimal(max(0, (c["expiry"] - asof).days)) / Decimal(365)
                with localcontext() as ctx:
                    ctx.prec = 50
                    rate = (ONE + D(tsy_y["value"]) / HUNDRED).ln()
                prem = black_scholes(c["option_type"], D(ln["value"]), D(c["strike"]), ty, rate, D(iv["value"]) / HUNDRED)
                inputs.append({**_q(iv, f"Implied vol — {c['name']}"),
                               "derived": f"premium {dstr(prem.quantize(BP))} from the entered implied vol "
                                          "(Black–Scholes, no dividends — estimate)"})
                total += sign * D(x["ratio"]) * prem / l0
                continue
            if iv:
                missing.append(f"to price {c['name']} from its implied vol: the current level and the Treasury yield")
            else:
                missing.append(f"the premium for {c['name']} (a premium quote, or its implied vol)")
    method = ("estimate per 100 of principal: underlying at current ÷ start level; options at premium ÷ start level "
              "(or Black–Scholes from the contract's own entered implied vol, no dividends); Treasury at face × "
              "(1 + yield)^−(days ÷ 365); embedded fee = note price − replica cost. No surface is fitted, nothing "
              "extrapolated.")
    if missing:
        return {"status": "not_priced", "note_price_pct": dstr(D(nq["value"])) if nq else None,
                "note_price_quote_id": str(nq["id"]) if nq else None, "replica_cost_pct": None,
                "embedded_fee_pct": None, "inputs": inputs, "missing": missing, "as_of": None, "method": method,
                "text": f"{NOT_PRICED} — missing: " + "; ".join(missing)}
    cost = (total * HUNDRED).quantize(BP, rounding=ROUND_HALF_EVEN)
    price = D(nq["value"])
    as_of = min(datetime.fromisoformat(i["as_of"]) for i in inputs if i.get("as_of"))
    return {"status": "priced", "note_price_pct": dstr(price), "note_price_quote_id": str(nq["id"]),
            "replica_cost_pct": dstr(cost), "embedded_fee_pct": dstr(price - cost), "inputs": inputs, "missing": [],
            "as_of": as_of.isoformat(), "method": method,
            "text": f"Embedded fee estimate {dstr(price - cost)} per 100 of principal (note {dstr(price)} − replica "
                    f"{dstr(cost)}), as of {as_of.isoformat()} — {ESTIMATE}"}


async def price(pool, *, org_id, actor_id, replica_id, start_level_quote_id=None,
                treasury_global_security_id=None) -> dict:
    """Price the replica now: a NEW comparison row (insert-only), priced or "not priced" naming the gap."""
    async with pool.acquire() as conn:
        actor = await HT.assert_human(conn, actor_id, org_id)
        r = await load_replica(conn, org_id, replica_id)
    if (start_level_quote_id or treasury_global_security_id or r["start_level"] is None) and r["status"] == "draft":
        r = await _price_draft_levels(pool, org_id=org_id, actor_id=actor["id"], r=r,
                                      start_level_quote_id=start_level_quote_id,
                                      treasury_global_security_id=treasury_global_security_id)
    elif (start_level_quote_id or treasury_global_security_id) and r["status"] != "draft":
        raise NoteConflict(f"the replica is {r['status']}; levels change only on a draft")
    async with pool.acquire() as conn:
        legs = await legs_of(conn, r["id"])
        t = (await live_terms(conn, terms_ids=[r["note_terms_id"]]) or [None])[0]
        res = await compute_cost(conn, org_id, r, legs)
        diffs = differences(t or {}, r["mapping"], approx=_loads(r["approximation"]))
        async with conn.transaction():
            row = await conn.fetchrow(
                f"""INSERT INTO {T_CMP} (org_id, replica_id, global_security_id, status, note_price_pct,
                        note_price_quote_id, replica_cost_pct, embedded_fee_pct, inputs, missing, differences, method,
                        as_of, computed_by)
                    VALUES ($1::uuid, $2::uuid, $3::uuid, $4, $5, $6::uuid, $7, $8, $9::jsonb, $10::jsonb, $11::jsonb,
                            $12, $13, $14::uuid) RETURNING *""",
                str(org_id), str(r["id"]), str(r["global_security_id"]), res["status"], D(res["note_price_pct"]),
                res["note_price_quote_id"], D(res["replica_cost_pct"]), D(res["embedded_fee_pct"]),
                _dumps(res["inputs"]), _dumps(res["missing"]), _dumps(diffs), res["method"],
                datetime.fromisoformat(res["as_of"]) if res["as_of"] else None, str(actor["id"]))
            await _audit(conn, org_id, actor["id"], "replica_priced", r["id"],
                         {"comparison": str(row["id"]), "status": res["status"], "missing": res["missing"]})
    return comparison_out(dict(row))


async def price_automatic(pool, *, org_id, replica_id) -> dict:
    """rep12 — the automatic check prices a live replica with NO person: the same :func:`compute_cost` (rep11's
    math, every input listed) and the same insert-only comparison row, marked computed_source 'automatic_check'
    with computed_by NULL. Never sets a level or a Treasury (that is a person's step on the draft): whatever is
    missing is recorded as "not priced", naming each gap."""
    async with pool.acquire() as conn:
        r = await load_replica(conn, org_id, replica_id)
        legs = await legs_of(conn, r["id"])
        t = (await live_terms(conn, terms_ids=[r["note_terms_id"]]) or [None])[0]
        res = await compute_cost(conn, org_id, r, legs)
        diffs = differences(t or {}, r["mapping"], approx=_loads(r["approximation"]))
        async with conn.transaction():
            row = await conn.fetchrow(
                f"""INSERT INTO {T_CMP} (org_id, replica_id, global_security_id, status, note_price_pct,
                        note_price_quote_id, replica_cost_pct, embedded_fee_pct, inputs, missing, differences, method,
                        as_of, computed_by, computed_source)
                    VALUES ($1::uuid, $2::uuid, $3::uuid, $4, $5, $6::uuid, $7, $8, $9::jsonb, $10::jsonb, $11::jsonb,
                            $12, $13, NULL, 'automatic_check') RETURNING *""",
                str(org_id), str(r["id"]), str(r["global_security_id"]), res["status"], D(res["note_price_pct"]),
                res["note_price_quote_id"], D(res["replica_cost_pct"]), D(res["embedded_fee_pct"]),
                _dumps(res["inputs"]), _dumps(res["missing"]), _dumps(diffs), res["method"],
                datetime.fromisoformat(res["as_of"]) if res["as_of"] else None)
            await _audit(conn, org_id, None, "replica_priced", r["id"],
                         {"comparison": str(row["id"]), "status": res["status"], "missing": res["missing"],
                          "source": "automatic_check"})
    return comparison_out(dict(row))


def comparison_out(c: dict | None) -> dict | None:
    if not c:
        return None
    missing = _loads(c["missing"]) or []
    return {"id": str(c["id"]), "replica_id": str(c["replica_id"]), "status": c["status"],
            "status_label": COMPARISON_STATUS_LABELS[c["status"]], "note_price_pct": dstr(D(c["note_price_pct"])),
            "replica_cost_pct": dstr(D(c["replica_cost_pct"])), "embedded_fee_pct": dstr(D(c["embedded_fee_pct"])),
            "inputs": _loads(c["inputs"]) or [], "missing": missing, "differences": _loads(c["differences"]) or [],
            "method": c["method"], "as_of": _iso(c["as_of"]), "created_at": _iso(c["created_at"]), "label": ESTIMATE,
            "computed_source": c.get("computed_source") or "person",
            "text": (f"{NOT_PRICED} — missing: " + "; ".join(missing)) if c["status"] == "not_priced" else
            f"Embedded fee estimate {dstr(D(c['embedded_fee_pct']))} per 100 of principal, as of {_iso(c['as_of'])}"}


async def latest_comparison(conn, org_id, replica_id) -> dict | None:
    row = await conn.fetchrow(f"SELECT * FROM {T_CMP} WHERE org_id = $1::uuid AND replica_id = $2::uuid AND "
                              "valid_to IS NULL AND system_to IS NULL ORDER BY created_at DESC LIMIT 1",
                              str(org_id), str(replica_id))
    return dict(row) if row else None


async def replica_detail(conn, org_id, replica_id) -> dict:
    r = await load_replica(conn, org_id, replica_id)
    legs = await legs_of(conn, r["id"])
    spec = spec_in(_loads(r["spec"]))
    und = await OS._security(conn, r["underlying_global_security_id"])
    label = und["ticker"] or und["name"]
    contracts = {str(x["contract_id"]): await OS.load_contract(conn, contract_id=x["contract_id"])
                 for x in legs if x["contract_id"]}
    step = await OS._setting(conn, org_id, STEP_SETTING, DEFAULT_STEP, lo=Decimal("0.005"), hi=Decimal("0.25"))
    task = await HT.get_task(conn, org_id, r["approval_task_id"]) if r["approval_task_id"] else None
    note = await OS._security(conn, r["global_security_id"])
    return {"replica": replica_out(r), "note": {"id": str(note["id"]), "name": note["name"]},
            "underlying": {"id": str(und["id"]), "name": und["name"], "ticker": und["ticker"],
                           "security_type": und["security_type"]},
            "legs": [{**_leg_brief(x), "id": str(x["id"]), "leg_no": x["leg_no"],
                      "strike_level": dstr(D(x["strike_level"])), "expiry": _iso(x["expiry"]),
                      "maturity": _iso(x["maturity"]), "contract": OS.contract_out(contracts.get(str(x["contract_id"]))),
                      "instruction": instruction_text(x, underlying=label, start_level=D(r["start_level"]),
                                                      contract=contracts.get(str(x["contract_id"])))} for x in legs],
            "payoff": {"step": dstr(step), "points": payoff_points(spec, legs, step=step),
                       "note": "at expiry, from strikes only — premiums are the replica's cost, not its payoff"},
            "computed": computed_terms(legs, spec), "approximation": _loads(r["approximation"]),
            "comparison": comparison_out(await latest_comparison(conn, org_id, r["id"])),
            "needs": needs(r, legs), "task": task, "manual_only": "Nothing trades: every leg is a manual instruction a "
                                                                   "person carries out."}


def needs(r: dict, legs: list[dict]) -> list[str]:
    """What the replica still needs before it can be submitted (empty = ready)."""
    out = []
    if r["start_level"] is None:
        out.append(f"{NOT_PRICED}: the underlying's level on {_iso(r['outcome_start'])} (sets every strike level)")
    if any(x["kind"] == "option" and x["contract_id"] is None for x in legs):
        out.append("every option leg's contract (set the start level)")
    return out


# ── maker-checker: a replica becomes the firm's alternative only on approval ─
async def submit(pool, *, org_id, actor_id, replica_id) -> dict:
    async with pool.acquire() as conn:
        actor = await HT.assert_human(conn, actor_id, org_id)
        r = await load_replica(conn, org_id, replica_id)
        legs = await legs_of(conn, r["id"])
        note = await OS._security(conn, r["global_security_id"])
    if r["status"] != "draft":
        raise NoteConflict(f"the replica is {r['status']}, not a draft")
    n = needs(r, legs)
    if n:
        raise NoteError("not ready to submit: " + "; ".join(n))
    task = await HT.open_task(pool, org_id=org_id, kind=TASK_APPROVAL, subject_type=SUBJECT, subject_id=r["id"],
                              maker_id=actor["id"], eligible_permission=APPROVE,
                              title=f"Approve the replica of {note['name']}",
                              payload={"replica_id": str(r["id"]), "mapping": r["mapping"]},
                              action_path=f"/portfolio/replication/notes/replicas/{r['id']}")
    makers = [str(m) for m in r["maker_ids"] or []]
    if str(actor["id"]) not in makers:
        makers.append(str(actor["id"]))
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                cur = await load_replica(conn, org_id, r["id"], lock=True)
                if cur["status"] != "draft":
                    raise NoteConflict(f"the replica is {cur['status']}, not a draft")
                await _update(conn, r["id"], {"status": "pending_approval", "approval_task_id": task["id"],
                                              "maker_ids": makers, "decision_note": None})
                await _audit(conn, org_id, actor["id"], "replica_submitted", r["id"], {"task": task["id"]})
    except Exception:
        from services import rep10_common as C

        await C.withdraw_task(pool, task["id"], "the replica could not be submitted")
        raise
    async with pool.acquire() as conn:
        return await replica_detail(conn, org_id, r["id"])


async def _apply(pool, task: dict, outcome: str, actor_id, note) -> dict | None:
    org_id = task["org_id"]
    async with pool.acquire() as conn:
        async with conn.transaction():
            r = await conn.fetchrow(f"SELECT * FROM {T_REP} WHERE id = $1::uuid AND org_id = $2::uuid AND "
                                    "system_to IS NULL FOR UPDATE", task["subject_id"], str(org_id))
            if r is None or r["status"] != "pending_approval":
                return replica_out(dict(r)) if r else None
            checker = uuid.UUID(str(actor_id))
            if checker in set(r["maker_ids"] or []):
                raise MakerCheckerViolation("maker-checker: a maker of this replica cannot decide it")
            now = datetime.now(timezone.utc)
            if outcome == "approve":
                prev = await conn.fetchrow(f"SELECT id FROM {T_REP} WHERE org_id = $1::uuid AND global_security_id = "
                                           "$2::uuid AND status = 'approved' AND system_to IS NULL FOR UPDATE",
                                           str(org_id), r["global_security_id"])
                if prev:
                    await _update(conn, prev["id"], {"status": "archived",
                                                     "status_reason": "superseded by a newer approved replica"})
                new = await _update(conn, r["id"], {"status": "approved", "checker_id": checker, "decided_at": now,
                                                    "decision_note": note})
            else:
                new = await _update(conn, r["id"], {"status": "draft", "approval_task_id": None,
                                                    "decision_note": note, "decided_at": now})
            await _audit(conn, org_id, checker, f"replica_{'approved' if outcome == 'approve' else 'rejected'}",
                         r["id"], {"task": task["id"], "note": note})
    return replica_out(new)


async def _on_complete(pool, task, outcome, actor_id, note):
    return await _apply(pool, task, outcome, actor_id, note)


HT.register_kind(TASK_APPROVAL, label="Note replica approval", outcomes=("approve", "reject"), maker_checker=True,
                 on_complete=_on_complete)


async def decide(pool, *, org_id, actor_id, replica_id, outcome: str, note: str | None = None) -> dict:
    if outcome not in ("approve", "reject"):
        raise NoteError("outcome is approve or reject")
    if outcome == "reject" and not (note or "").strip():
        raise NoteError("a rejection needs a note saying why")
    async with pool.acquire() as conn:
        actor = await HT.assert_human(conn, actor_id, org_id)
        r = await load_replica(conn, org_id, replica_id)
    if r["status"] != "pending_approval":
        raise NoteConflict(f"the replica is {r['status']}, not awaiting approval")
    if str(actor["id"]) in {str(m) for m in r["maker_ids"] or []}:
        raise MakerCheckerViolation(f"maker-checker: you made or priced this replica, so a different holder of "
                                    f"{APPROVE} must {outcome} it")
    from services import rep10_common as C

    try:
        await C.complete(pool, org_id=org_id, task_id=r["approval_task_id"], actor_id=actor["id"], outcome=outcome,
                         note=note)
    except C.MakerCheckerViolation as exc:
        raise MakerCheckerViolation(str(exc)) from exc
    except C.Rep10Conflict as exc:
        raise NoteConflict(str(exc)) from exc
    async with pool.acquire() as conn:
        return await replica_detail(conn, org_id, r["id"])


async def archive(pool, *, org_id, actor_id, replica_id, reason: str) -> dict:
    if not (reason or "").strip():
        raise NoteError("archiving a replica needs a reason")
    async with pool.acquire() as conn:
        actor = await HT.assert_human(conn, actor_id, org_id)
        async with conn.transaction():
            r = await load_replica(conn, org_id, replica_id, lock=True)
            if r["status"] not in ("draft", "approved"):
                raise NoteConflict(f"the replica is {r['status']}; a draft or an approved replica is archived")
            await _update(conn, r["id"], {"status": "archived", "status_reason": reason.strip()})
            await _audit(conn, org_id, actor["id"], "replica_archived", r["id"], {"reason": reason.strip()})
        return await replica_detail(conn, org_id, r["id"])


def actions_for(r: dict, *, can_manage: bool, can_approve: bool, user_id, needs_: list) -> list[str]:
    out = []
    makers = {str(m) for m in r.get("maker_ids") or []}
    if r["status"] == "draft" and can_manage:
        out += ["price"] + (["submit"] if not needs_ else []) + ["archive"]
    if r["status"] == "approved" and can_manage:
        out += ["price", "archive"]
    if r["status"] == "pending_approval" and can_approve and str(user_id) not in makers:
        out += ["approve", "reject"]
    return out


# ═══ The X-ray marker and member visibility ════════════════════════════════
async def xray_notes(conn, org_id, holdings: list[dict]) -> list[dict]:
    """Each held structured note: verdict, stated terms, distribution / fees when known, and — when the firm has
    an APPROVED replica — "replicable alternative" with its latest comparison (figures stripped for members by
    :func:`member_filter`)."""
    secs = sorted({h["security_id"] for h in holdings if h.get("security_id")})
    if not secs:
        return []
    rows = {str(t["global_security_id"]): t for t in current_of(await live_terms(conn, note_ids=secs))}
    reads = await readings_of(conn, {t["reference_filing_id"] for t in rows.values()})
    out = []
    for h in holdings:
        t = rows.get(h.get("security_id") or "")
        if t is None:
            continue
        v = verdict_out(await verdict_of(conn, t["id"]))
        rep = await conn.fetchrow(f"SELECT * FROM {T_REP} WHERE org_id = $1::uuid AND global_security_id = $2::uuid "
                                  "AND status = 'approved' AND system_to IS NULL", str(org_id),
                                  str(t["global_security_id"]))
        cmp_ = comparison_out(await latest_comparison(conn, org_id, rep["id"])) if rep else None
        out.append({"security_id": str(t["global_security_id"]), "label": h.get("label"), "account": h.get("account"),
                    "account_id": h.get("account_id"), "market_value": h.get("market_value"),
                    "terms": terms_out(t), "verdict": v,
                    "distribution": _distribution(reads.get(str(t["reference_filing_id"]))),
                    "replicable_alternative": ({"replica_id": str(rep["id"]), "label": "replicable alternative",
                                                "link": f"/portfolio/replication/notes/replicas/{rep['id']}",
                                                "mapping_label": MAPPING_LABELS[rep["mapping"]]} if rep else None),
                    "alternative_text": None if rep else (
                        "no approved replica" if (v or {}).get("verdict") in ("static", "approximate")
                        else "not replicable with listed options"),
                    "estimate": cmp_})
    return out


def member_filter(rows: list[dict], *, staff: bool, gate: dict) -> list[dict]:
    """Replica cost and embedded-fee figures are hypothetical (rep10's policy): staff always; a member only when
    the approved policy's audience is staff_and_members — with its disclosure. Verdict and terms stay."""
    if staff or gate.get("members_allowed") is True:
        return [{**r, "estimate_disclosure": None if staff else gate.get("disclosure")} for r in rows]
    out = []
    for r in rows:
        r = dict(r)
        if r.get("estimate") is not None:
            r["estimate"] = {"withheld": True, "text": gate.get("disclosure")}
        out.append(r)
    return out


def strip_comparison(detail: dict, *, staff: bool, gate: dict) -> dict:
    if staff or gate.get("members_allowed") is True:
        return {**detail, "estimate_disclosure": None if staff else gate.get("disclosure")}
    d = dict(detail)
    if d.get("comparison") is not None:
        d["comparison"] = {"withheld": True, "text": gate.get("disclosure")}
    return d


def vocabularies() -> dict:
    def pairs(d):
        return [{"key": k, "label": v} for k, v in d.items()]

    return {"verdicts": pairs(VERDICT_LABELS), "mappings": pairs(MAPPING_LABELS),
            "replica_statuses": pairs(STATUS_LABELS), "leg_kinds": pairs(LEG_KIND_LABELS),
            "archetypes": pairs(ARCHETYPE_LABELS), "basket_types": pairs(BASKET_LABELS),
            "protection_types": pairs(PROTECTION_LABELS), "comparison_statuses": pairs(COMPARISON_STATUS_LABELS),
            "quote_kinds": [{"key": k, "label": OS.QUOTE_KIND_LABELS[k]} for k in
                            ("note_price", "treasury_yield", "index_level", "underlying_price", "premium",
                             "implied_vol")],
            "implied_vol_unit": "percent a year (18.5 = 18.5%)", "not_priced": NOT_PRICED,
            "preliminary": PRELIMINARY, "estimate_label": ESTIMATE, "counsel_items": list(COUNSEL_ITEMS),
            "ssvi": {"used": False, "min_quotes_per_slice": SSVI_MIN_PER_SLICE, "max_years": dstr(SSVI_MAX_YEARS),
                     "text": f"No volatility surface is fitted: the SSVI engine needs call / put price pairs, at least "
                             f"{SSVI_MIN_PER_SLICE} per expiry, and refuses maturities beyond {dstr(SSVI_MAX_YEARS)} "
                             "years. A contract is priced only from its own premium or its own entered implied vol."}}
