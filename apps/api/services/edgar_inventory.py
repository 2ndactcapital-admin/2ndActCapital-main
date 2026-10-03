"""The TEMPLATE STUDY inventory pass (edgarcohorts) — inventory, NOT extraction.

Before the extraction schema is settled, list EVERYTHING a small, varied set of
filings actually contains, so fields are added once, from evidence, instead of
one surprise at a time.

INPUT. For each chosen document, its TERMS PAGES only (``terms_pages``): from
the start through the payout examples, stopping before risk factors, index
methodology, licence text and tax. The kept character count and a token
estimate are recorded per document.

DOCUMENTS (``select_documents``). From a cohort (normally the template-study
preset): per issuer group, 4-6 FINAL pricing supplements chosen for VARIETY —
greedy over product families found by keyword (autocall, buffer, barrier,
digital / fixed payment, contingent coupon, participation / leverage), then
era — plus 1-2 product supplements, because notes lean on them for definitions.

THE MODEL lists every distinct data element: the label exactly as written, the
value as written, a short EXACT quote, the section, and the existing schema
field it corresponds to (the field list is generated from
``portfolio.note_terms_field_registry``) or NEW; and flags labels that are
misleading about their meaning. Per document it also reports the product family
as described, the program / product supplement cited, whether a hypothetical
payout table is present, and the issue size.

EVERY QUOTE IS CHECKED against the filing's text (``FilingDocument.index``,
the same whitespace-normalised locator B1 uses). An item whose quote is not
found is REJECTED — stored on the document row with the reason, and counted —
never as an item.

CONCEPTS (``aggregate``). Items are grouped first by normalised label, then by
ONE model-assisted grouping call over the list of unique labels. Every item
keeps its original label and quote; a concept records its synonyms, the issuers
using it, frequency, example values, the mapped or proposed field, and every
misleading-label flag. ``label_dictionary`` gives issuer -> label -> concept
for rules.

THE MODEL is chosen at run time (``choose_model``) from
``platform_model_catalog`` rows that are 'available', NOT Claude, not an
embedding provider and not an OpenRouter route, and that the LiteLLM proxy
actually serves under that exact name. None -> BLOCKED. Every call goes through
``services.note_extraction.proxy`` — the one HTTP chokepoint (no fallbacks,
provider-reported model recorded); never a provider directly.

COST. A hard spending cap (``SpendTracker``: every call reserves its estimate
first and is never made if it would cross the cap; the run then stops cleanly
as ``stopped_spend_cap``). ``dry_run`` plans documents, terms-page sizes and an
estimated cost and makes ZERO model calls.
"""
from __future__ import annotations

import json
import re
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

from services.database import platform_scope
from services.edgar_cohorts import era_of
from services.note_extraction import proxy, trim
from services.note_extraction.spend import (
    SpendCapReached, SpendTracker, estimate_call_cost, priced_cost,
)
from services.note_extraction.trim import estimate_tokens_chars

PROMPT_VERSION = "edgarcohorts.inventory.v1"
GROUPING_PROMPT_VERSION = "edgarcohorts.grouping.v1"
NEW = "NEW"
MAX_TERMS_CHARS = 120_000
DEFAULT_MAX_TOKENS = 8000
GROUPING_MAX_TOKENS = 8000
PER_ISSUER_MIN, PER_ISSUER_MAX = 4, 6
PRODUCT_SUPPLEMENTS_PER_ISSUER = 2
FETCHED_PRICING = ("ready_for_extraction", "prefilter_skipped")
EMBEDDING_PROVIDERS = frozenset({"voyage"})

# Calls made by this module (dry runs assert this does not move).
CALLS = {"inventory": 0, "grouping": 0}


class InventoryBlocked(RuntimeError):
    """No eligible model: nothing can run."""


# ═══ Terms pages ═══════════════════════════════════════════════════════════
_H = lambda *alts: re.compile(r"^(?:\d+\.\s+|[•■▪\-\*]\s*)?(?:" + "|".join(alts) + r")", re.IGNORECASE)

STOP_HEADINGS: tuple[tuple[str, re.Pattern], ...] = (
    ("risk_factors", _H(r"(?:selected\s+|key\s+|additional\s+|summary\s+)?risk\s+(?:factors|considerations)",
                        r"risks?\s+relating\s+to", r"key\s+risks")),
    ("tax", _H(r"(?:material\s+|certain\s+)?(?:u\.\s?s\.\s+|united\s+states\s+)?federal\s+income\s+tax",
               r"(?:material\s+|certain\s+)?(?:u\.\s?s\.\s+)?tax\s+(?:consequences|considerations|treatment|discussion)",
               r"supplemental\s+(?:discussion\s+of\s+)?(?:u\.\s?s\.\s+)?federal\s+income\s+tax",
               r"canadian\s+federal\s+income\s+tax", r"taxation")),
    ("index_methodology", _H(r"(?:the\s+)?(?:index|indices|underlying)\s+(?:methodology|description|information)",
                             r"description\s+of\s+the\s+(?:index|indices|underlyings?|reference\s+assets?)",
                             r"information\s+(?:about|regarding|relating\s+to)\s+the\s+(?:index|indices|underlyings?|reference)",
                             r"(?:the\s+)?underlying\s+(?:index|indices)\s*$",
                             r"historical\s+(?:information|performance|data|closing)")),
    ("license", _H(r"licens(?:e|ing)(?:\s+agreements?)?\b", r"(?:index\s+)?disclaimers?\s*$",
                   r"trademarks?\b")),
)
EXAMPLE_HEADINGS = _H(
    r"hypothetical\s+(?:examples?|payments?|payouts?|returns?|amounts?|payment\s+at\s+maturity)",
    r"(?:examples?|illustrations?)\s+of\s+(?:hypothetical\s+)?(?:payments?|payouts?|amounts?|returns?|calculations?)",
    r"what\s+is\s+the\s+total\s+return", r"scenario\s+analysis",
)


def _heading_kind(line: str) -> tuple[str, str] | None:
    s = line.strip()
    if not s or len(s) > trim.MAX_HEADING_LEN:
        return None
    if s.endswith(".") and not s.lower().endswith(("inc.", "co.")):
        return None
    for name, rx in STOP_HEADINGS:
        if rx.match(s):
            return "stop", name
    if EXAMPLE_HEADINGS.match(s):
        return "example", "payout_examples"
    c = trim.classify_heading(s)
    if c:
        return "other", c[1]
    return None


@dataclass
class TermsPages:
    text: str
    chars: int
    tokens_est: int
    full_chars: int
    sections: list[dict]
    stopped_at: str | None


def terms_pages(text: str, *, max_chars: int = MAX_TERMS_CHARS) -> TermsPages:
    """From the start through the payout examples, stopping before risk
    factors, index methodology, licence text and tax.

    Everything before the FIRST stop heading is kept. Some issuers place the
    hypothetical payout examples after a risk section; a payout-examples
    section found later is appended, up to the next recognised heading.
    """
    marks: list[tuple[int, str, str, str]] = []
    pos = 0
    for line in text.splitlines(keepends=True):
        k = _heading_kind(line)
        if k:
            marks.append((pos, k[0], k[1], line.strip()))
        pos += len(line)
    first_stop = next((m for m in marks if m[1] == "stop"), None)
    end = first_stop[0] if first_stop else len(text)
    parts = [(0, end, "opening_to_first_stop")]
    if first_stop:
        for i, m in enumerate(marks):
            if m[0] > end and m[1] == "example":
                nxt = next((x[0] for x in marks[i + 1:] if x[1] != "example"), len(text))
                parts.append((m[0], nxt, "payout_examples"))
                break
    kept = "\n\n[…]\n\n".join(text[a:b].strip("\n") for a, b, _ in parts if text[a:b].strip())
    kept = kept[:max_chars]
    return TermsPages(
        text=kept, chars=len(kept), tokens_est=estimate_tokens_chars(len(kept)), full_chars=len(text),
        sections=[{"start": a, "end": b, "part": name} for a, b, name in parts],
        stopped_at=(f"{first_stop[2]}: {first_stop[3][:80]}" if first_stop else None),
    )


# ═══ Variety: product families by keyword ══════════════════════════════════
FAMILY_PATTERNS: dict[str, re.Pattern] = {
    "autocall": re.compile(r"automatic(?:ally)?\s+(?:call|redeem|redemption|early)|auto-?call", re.I),
    "buffer": re.compile(r"\bbuffer", re.I),
    "barrier": re.compile(r"\bbarrier|knock-?in|trigger\s+(?:level|value|price)|downside\s+threshold", re.I),
    "digital_fixed_payment": re.compile(r"\bdigital\b|fixed\s+(?:payment|return)|contingent\s+(?:fixed\s+)?return", re.I),
    "contingent_coupon": re.compile(r"contingent\s+(?:coupon|interest|payment)", re.I),
    "participation_leverage": re.compile(r"participation\s+rate|leverage(?:d)?\s+(?:factor|return)|upside\s+(?:participation|leverage)", re.I),
}


def families_of(text: str) -> list[str]:
    return [k for k, rx in FAMILY_PATTERNS.items() if rx.search(text or "")]


@dataclass
class ChosenDocument:
    reference_filing_id: str
    accession_number: str
    issuer_group: str
    document_kind: str
    filing_date: object
    families: list[str]
    reason: str
    doc: object = None            # FilingDocument
    terms: TermsPages | None = None


async def _cohort_documents(conn, cohort_id) -> list[dict]:
    async with platform_scope(conn):
        rows = await conn.fetch(
            """SELECT m.position, f.accession_number, f.reference_filing_id, f.document_kind,
                      f.pipeline_status, f.filing_date,
                      COALESCE(i.issuer_group, '(no listed issuer)') AS issuer_group
               FROM portfolio.edgar_cohort_members m
               JOIN portfolio.edgar_index_filings f ON f.accession_number = m.accession_number
               LEFT JOIN portfolio.structured_note_issuers i ON i.filer_cik = f.primary_issuer_cik
               WHERE m.cohort_id = $1 AND f.reference_filing_id IS NOT NULL
                 AND ((f.document_kind = 'pricing_supplement' AND f.pipeline_status = ANY($2::text[]))
                      OR f.document_kind = 'product_supplement')
               ORDER BY m.position""",
            cohort_id, list(FETCHED_PRICING))
    return [dict(r) for r in rows]


def choose_for_variety(cands: list[ChosenDocument], *, lo: int = PER_ISSUER_MIN,
                       hi: int = PER_ISSUER_MAX) -> list[ChosenDocument]:
    """Greedy, deterministic: each pick adds the most not-yet-covered product
    families, then a not-yet-covered era, then cohort order. Stops at ``hi``,
    or once ``lo`` are chosen and no remaining candidate adds anything new."""
    chosen: list[ChosenDocument] = []
    fam, eras = set(), set()
    rest = list(cands)
    while rest and len(chosen) < hi:
        def gain(c):
            return (len(set(c.families) - fam), int(era_of(c.filing_date) not in eras))
        best = max(rest, key=lambda c: (gain(c), -rest.index(c)))
        g = gain(best)
        if len(chosen) >= lo and g == (0, 0):
            break
        new_f = sorted(set(best.families) - fam)
        best.reason = (f"adds families {new_f}" if new_f else
                       f"adds era {era_of(best.filing_date)}" if g[1] else "fills the per-issuer minimum")
        chosen.append(best)
        fam |= set(best.families)
        eras.add(era_of(best.filing_date))
        rest.remove(best)
    return chosen


async def select_documents(conn, cohort_id, *, loader, lo: int = PER_ISSUER_MIN, hi: int = PER_ISSUER_MAX,
                           product_supplements: int = PRODUCT_SUPPLEMENTS_PER_ISSUER,
                           progress=None) -> tuple[list[ChosenDocument], list[dict]]:
    """``loader(conn, reference_filing_id) -> FilingDocument``. Returns
    (chosen documents, documents that could not be loaded)."""
    rows = await _cohort_documents(conn, cohort_id)
    by_issuer: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_issuer[r["issuer_group"]].append(r)
    chosen, unloadable = [], []
    for issuer in sorted(by_issuer):
        pricing, product = [], []
        for r in by_issuer[issuer]:
            if r["document_kind"] == "product_supplement" and len(product) >= product_supplements:
                continue
            try:
                doc = await loader(conn, r["reference_filing_id"])
            except Exception as exc:  # noqa: BLE001 — recorded, never fatal
                unloadable.append({"accession_number": r["accession_number"], "error": str(exc)[:200]})
                continue
            tp = terms_pages(doc.text)
            c = ChosenDocument(str(r["reference_filing_id"]), r["accession_number"], issuer,
                               r["document_kind"], r["filing_date"], families_of(tp.text), "", doc, tp)
            if r["document_kind"] == "product_supplement":
                c.reason = "product supplement (definitions)"
                product.append(c)
            else:
                pricing.append(c)
        picked = choose_for_variety(pricing, lo=lo, hi=hi)
        chosen += picked + product
        if progress:
            progress(issuer, f"{len(picked)} pricing supplements, {len(product)} product supplements")
    return chosen, unloadable


# ═══ Model choice ══════════════════════════════════════════════════════════
def _is_claude(*values) -> bool:
    return any(v and ("claude" in v.lower() or "anthropic" in v.lower()) for v in values)


def _is_openrouter(*values) -> bool:
    return any(v and "openrouter" in v.lower() for v in values)


async def eligible_models(conn, catalog: dict | None) -> tuple[list[str], list[dict]]:
    """(eligible model ids, every catalog row with why it is or is not)."""
    async with platform_scope(conn):
        rows = await conn.fetch(
            "SELECT model_id, display_name, provider, availability FROM platform_model_catalog ORDER BY model_id")
    ok, report = [], []
    for r in rows:
        dep = (catalog or {}).get(r["model_id"])
        why = None
        if r["availability"] != "available":
            why = f"availability {r['availability']}"
        elif _is_claude(r["model_id"], r["display_name"], r["provider"], dep and dep.upstream):
            why = "Claude models are ruled out for this work"
        elif _is_openrouter(r["model_id"], r["provider"], dep and dep.upstream):
            why = "OpenRouter routes are not used"
        elif (r["provider"] or "").lower() in EMBEDDING_PROVIDERS:
            why = "embedding provider"
        elif catalog is not None and dep is None:
            why = "not served by the LiteLLM proxy under this name"
        elif dep is not None and dep.duplicate:
            why = "more than one deployment behind this name (would load-balance)"
        report.append({"model_id": r["model_id"], "provider": r["provider"],
                       "availability": r["availability"], "eligible": why is None, "why_not": why})
        if why is None:
            ok.append(r["model_id"])
    return ok, report


async def choose_model(conn, catalog: dict | None, requested: str | None = None) -> tuple[str | None, list[dict]]:
    ok, report = await eligible_models(conn, catalog)
    if requested:
        return (requested if requested in ok else None), report
    return (ok[0] if ok else None), report


# ═══ Prompts ═══════════════════════════════════════════════════════════════
INSTRUCTIONS = """You are making an INVENTORY of one US structured-note filing (SEC 424B2). \
This is not extraction into a fixed form: list EVERY distinct data element the text below \
contains — every labelled term, date, level, rate, amount, schedule, party, identifier, \
condition and definition — even ones that fit no existing field.

For each element give:
- "label": the label EXACTLY as written in the filing (keep its capitalisation and wording)
- "value": the value as written (null if the element is a definition with no single value)
- "quote": a SHORT EXACT quote copied verbatim from the text (under 300 characters) that \
contains the element — it will be searched for in the filing and the item is discarded if \
it is not found
- "section": the heading of the section it appears under
- "maps_to": the key of the existing schema field below that holds the SAME meaning, or "NEW"
- "proposed_field_key": for NEW, a snake_case key you would propose; otherwise null
- "misleading_label": true when the label is misleading about what the element really does \
(for example a "Redemption Barrier" that is really only the threshold for a fixed payout and \
protects nothing); "misleading_note": one sentence saying what it really is

Also, for the document as a whole:
- "product_family": the product family as the DOCUMENT describes it, in its own words
- "program_supplement": the program / product supplement it cites (name and number), or null
- "has_hypothetical_payout_table": true or false
- "issue_size": the aggregate principal amount as written, or null

Answer with ONE JSON object only:
{"document": {"product_family": ..., "program_supplement": ..., \
"has_hypothetical_payout_table": ..., "issue_size": ...}, "items": [ {...}, ... ]}
No prose, no markdown."""

GROUPING_INSTRUCTIONS = """Below is a numbered list of the distinct labels found across many \
structured-note filings (already lower-cased and normalised), with an example value and the \
existing-field mapping most items gave. Group labels that mean THE SAME THING — synonyms used \
by different issuers — into concepts. Do not merge labels that differ in meaning, even if they \
sound alike (a buffer is not a barrier; a coupon barrier is not a downside threshold).

Answer with ONE JSON object only:
{"groups": [{"concept": "<short name>", "label_ids": [<ids>], "maps_to": "<existing field key or NEW>", \
"proposed_field_key": "<snake_case or null>"}]}
Every id should appear in exactly one group; a label with no synonyms is a group of one."""


def field_list_text(registry_rows: list[dict]) -> str:
    lines = [f"- {r['field_key']}: {r['display_label']} ({r['data_type']})" for r in registry_rows]
    return "Existing schema fields (key: label (type)):\n" + "\n".join(lines)


def build_inventory_messages(registry_rows: list[dict], terms_text: str, *, filer: str | None,
                             accession: str | None) -> list[dict]:
    system = INSTRUCTIONS + "\n\n" + field_list_text(registry_rows)
    user = (f"Filer: {filer or 'unknown'}\nAccession: {accession or 'unknown'}\n\n"
            f"FILING TEXT (terms pages):\n{terms_text}")
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


# ═══ Calls (through the proxy chokepoint only) ═════════════════════════════
@dataclass
class CallResult:
    status: str                    # ok | failed | model_mismatch | invalid
    parsed: dict | None = None
    error: str | None = None
    provider_model: str | None = None
    proxy_model_id: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float = 0.0
    latency_ms: int | None = None
    call_id: str = field(default_factory=lambda: str(uuid.uuid4()))


async def _call(deployment: str, messages: list[dict], *, catalog: dict, spend: SpendTracker,
                max_tokens: int, what: str, kind: str, tags: list[str]) -> CallResult:
    """One call. Reserves its estimated cost FIRST (raises SpendCapReached
    without calling); provenance checked like B1's readers."""
    dep = catalog.get(deployment)
    chars = sum(len(m["content"]) for m in messages)
    reservation = await spend.reserve(estimate_call_cost(dep, chars, max_tokens), what)
    body = {"model": deployment, "messages": messages, "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
            "metadata": {"tags": tags + [f"usage:{kind}", f"prompt:{PROMPT_VERSION}"]}}
    CALLS[kind] += 1
    resp = await proxy.chat(body)
    res = CallResult(status="failed", latency_ms=resp.latency_ms,
                     proxy_model_id=resp.headers.get("x-litellm-model-id"))
    res.input_tokens, res.output_tokens, _cached = proxy.response_usage(resp.body)
    actual = proxy.header_cost(resp)
    if actual is None:
        actual = priced_cost(dep, res.input_tokens, res.output_tokens, _cached)
    if resp.status != 200:
        actual = actual if actual is not None else 0.0
    res.cost_usd = await spend.settle(reservation, actual)
    if resp.status != 200 or resp.body is None:
        res.error = f"HTTP {resp.status}: {resp.error or (resp.text or '')[:300]}"
        return res
    res.provider_model = resp.body.get("model")
    fallbacks = resp.headers.get("x-litellm-attempted-fallbacks")
    if fallbacks not in (None, "0"):
        res.status, res.error = "model_mismatch", f"the proxy attempted {fallbacks} fallback(s)"
        return res
    upstream = dep.upstream if dep else None
    if not proxy.reported_model_matches(upstream, res.provider_model):
        res.status = "model_mismatch"
        res.error = f"asked for '{deployment}' ({upstream}) but the provider reported '{res.provider_model}'"
        return res
    if _is_claude(res.provider_model, upstream):
        res.status, res.error = "model_mismatch", "a Claude model answered; Claude is ruled out for this work"
        return res
    try:
        content = resp.body["choices"][0]["message"]["content"]
        parsed = json.loads(content)
        if not isinstance(parsed, dict):
            raise ValueError("not a JSON object")
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        res.status, res.error = "invalid", f"unparseable response: {exc}"[:300]
        return res
    res.status, res.parsed = "ok", parsed
    return res


# ═══ Parsing + quote verification ══════════════════════════════════════════
_NORM_RE = re.compile(r"[^a-z0-9%$#&/ ]+")


def normalise_label(label: str) -> str:
    s = (label or "").lower().replace("’", "'").replace("‘", "'")
    s = re.sub(r"\(s\)", "", s)
    s = _NORM_RE.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"^(?:the|a|an)\s+", "", s)
    return s


def _str_or_none(v) -> str | None:
    if v is None:
        return None
    if isinstance(v, (dict, list)):
        return json.dumps(v, default=str)[:2000]
    s = str(v).strip()
    return s[:2000] or None


def parse_items(parsed: dict, doc, registry_keys: set[str]) -> tuple[dict, list[dict], list[dict]]:
    """(document facts, accepted items, rejected items). An item whose quote
    is not found verbatim in the filing text is rejected with a reason."""
    meta = parsed.get("document") if isinstance(parsed.get("document"), dict) else {}
    facts = {
        "product_family": _str_or_none(meta.get("product_family")),
        "program_supplement": _str_or_none(meta.get("program_supplement")),
        "has_payout_table": meta.get("has_hypothetical_payout_table")
        if isinstance(meta.get("has_hypothetical_payout_table"), bool) else None,
        "issue_size": _str_or_none(meta.get("issue_size")),
    }
    accepted, rejected = [], []
    items = parsed.get("items") if isinstance(parsed.get("items"), list) else []
    for it in items:
        if not isinstance(it, dict):
            rejected.append({"item": str(it)[:200], "reason": "not an object"})
            continue
        label = _str_or_none(it.get("label"))
        quote = _str_or_none(it.get("quote"))
        if not label:
            rejected.append({"label": label, "quote": quote, "reason": "no label"})
            continue
        if not quote:
            rejected.append({"label": label, "quote": quote, "reason": "no quote"})
            continue
        span = doc.index.locate(quote)
        if span is None:
            rejected.append({"label": label, "quote": quote[:300], "reason": "quote not found in the filing"})
            continue
        maps_to = _str_or_none(it.get("maps_to")) or NEW
        mapped = maps_to if maps_to in registry_keys else None
        proposed = _str_or_none(it.get("proposed_field_key"))
        if maps_to != NEW and mapped is None and not proposed:
            proposed = maps_to   # the model named a field that does not exist: keep it as a proposal
        accepted.append({
            "label": label, "label_normalized": normalise_label(label) or label.lower(),
            "value_text": _str_or_none(it.get("value")), "quote": quote,
            "quote_char_start": span[0], "quote_char_end": span[1],
            "section": _str_or_none(it.get("section")), "mapped_field_key": mapped,
            "proposed_field_key": None if mapped else proposed,
            "misleading_label": it.get("misleading_label") is True,
            "misleading_note": _str_or_none(it.get("misleading_note")),
        })
    return facts, accepted, rejected


# ═══ Storage ═══════════════════════════════════════════════════════════════
def _dec(v):
    return None if v is None else Decimal(str(round(float(v), 8)))


async def create_run(conn, *, cohort_id, deployment, spend_cap, planned, created_by=None) -> str:
    async with platform_scope(conn):
        return str(await conn.fetchval(
            """INSERT INTO portfolio.edgar_inventory_runs
                   (cohort_id, deployment_name, prompt_version, spend_cap_usd, documents_planned, created_by)
               VALUES ($1, $2, $3, $4, $5, $6) RETURNING id""",
            cohort_id, deployment, PROMPT_VERSION, _dec(spend_cap), planned, created_by))


async def store_document(conn, run_id, c: ChosenDocument, res: CallResult, deployment: str,
                         facts: dict, accepted: list[dict], rejected: list[dict]) -> str:
    async with platform_scope(conn):
        doc_id = await conn.fetchval(
            """INSERT INTO portfolio.edgar_inventory_documents
                   (run_id, accession_number, reference_filing_id, document_kind, issuer_group,
                    selection_reason, status, error, terms_chars, terms_tokens_est, terms_sections,
                    product_family, program_supplement, has_payout_table, issue_size,
                    items_accepted, items_rejected, rejected_items, deployment_name, provider_model,
                    proxy_model_id, call_id, input_tokens, output_tokens, cost_usd, latency_ms)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11::jsonb,$12,$13,$14,$15,$16,$17,$18::jsonb,
                       $19,$20,$21,$22,$23,$24,$25,$26) RETURNING id""",
            run_id, c.accession_number, c.reference_filing_id, c.document_kind, c.issuer_group,
            c.reason, res.status, res.error, c.terms.chars, c.terms.tokens_est,
            json.dumps(c.terms.sections), facts.get("product_family"), facts.get("program_supplement"),
            facts.get("has_payout_table"), facts.get("issue_size"), len(accepted), len(rejected),
            json.dumps(rejected, default=str), deployment, res.provider_model, res.proxy_model_id,
            res.call_id, res.input_tokens, res.output_tokens, _dec(res.cost_usd), res.latency_ms)
        if accepted:
            await conn.executemany(
                """INSERT INTO portfolio.edgar_inventory_items
                       (run_id, document_id, accession_number, issuer_group, label, label_normalized,
                        value_text, quote, quote_char_start, quote_char_end, section, mapped_field_key,
                        proposed_field_key, misleading_label, misleading_note, deployment_name,
                        provider_model, call_id)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18)""",
                [(run_id, doc_id, c.accession_number, c.issuer_group, a["label"], a["label_normalized"],
                  a["value_text"], a["quote"], a["quote_char_start"], a["quote_char_end"], a["section"],
                  a["mapped_field_key"], a["proposed_field_key"], a["misleading_label"],
                  a["misleading_note"], deployment, res.provider_model, res.call_id) for a in accepted])
    return str(doc_id)


async def finish_run(conn, run_id, **fields) -> None:
    allowed = {"status", "spent_usd", "documents_done", "items_accepted", "items_rejected",
               "grouping_method", "stop_reason", "report", "finished_at"}
    assert set(fields) <= allowed, set(fields) - allowed
    cols, vals = [], []
    for i, (k, v) in enumerate(fields.items(), start=2):
        if k == "report":
            cols.append(f"report = ${i}::jsonb")
            v = json.dumps(v, default=str)
        else:
            cols.append(f"{k} = ${i}")
            if k == "spent_usd":
                v = _dec(v)
        vals.append(v)
    async with platform_scope(conn):
        await conn.execute(f"UPDATE portfolio.edgar_inventory_runs SET {', '.join(cols)} WHERE id = $1",
                           run_id, *vals)


# ═══ The run ═══════════════════════════════════════════════════════════════
@dataclass
class InventorySummary:
    run_id: str | None
    status: str
    dry_run: bool
    deployment: str | None
    documents_planned: int = 0
    documents_done: int = 0
    items_accepted: int = 0
    items_rejected: int = 0
    spent_usd: float = 0.0
    est_cost_usd: float = 0.0
    stop_reason: str | None = None
    plan: list[dict] = field(default_factory=list)
    unloadable: list[dict] = field(default_factory=list)
    model_report: list[dict] = field(default_factory=list)


async def run_inventory(conn, cohort_id, *, catalog: dict, spend_cap_usd: float, dry_run: bool,
                        loader, registry_rows: list[dict], deployment: str | None = None,
                        created_by=None, max_tokens: int = DEFAULT_MAX_TOKENS,
                        lo: int = PER_ISSUER_MIN, hi: int = PER_ISSUER_MAX,
                        product_supplements: int = PRODUCT_SUPPLEMENTS_PER_ISSUER,
                        group_with_model: bool = True, progress=None) -> InventorySummary:
    """Plan (dry run) or run the inventory pass over a cohort.

    ``deployment``: a model id the caller already picked; otherwise
    ``choose_model``. No eligible model -> status 'blocked' (a dry run still
    plans, so the token counts are visible)."""
    chosen_model, model_report = await choose_model(conn, catalog, deployment)
    docs, unloadable = await select_documents(conn, cohort_id, loader=loader, lo=lo, hi=hi,
                                              product_supplements=product_supplements, progress=progress)
    summary = InventorySummary(None, "planned" if dry_run else "running", dry_run, chosen_model,
                               documents_planned=len(docs), unloadable=unloadable, model_report=model_report)
    dep = catalog.get(chosen_model) if chosen_model else None
    for c in docs:
        msgs = build_inventory_messages(registry_rows, c.terms.text, filer=c.issuer_group,
                                        accession=c.accession_number)
        chars = sum(len(m["content"]) for m in msgs)
        est = estimate_call_cost(dep, chars, max_tokens)
        summary.est_cost_usd += est
        summary.plan.append({"accession_number": c.accession_number, "issuer_group": c.issuer_group,
                             "document_kind": c.document_kind, "families": c.families, "reason": c.reason,
                             "terms_chars": c.terms.chars, "terms_tokens_est": c.terms.tokens_est,
                             "full_chars": c.terms.full_chars, "stopped_at": c.terms.stopped_at,
                             "est_cost_usd": round(est, 6)})
    if chosen_model is None:
        summary.status = "blocked"
        summary.stop_reason = ("BLOCKED: no platform_model_catalog entry is available, non-Claude, and "
                               "served by the proxy" + (f" (requested {deployment!r} is not eligible)"
                                                        if deployment else ""))
        return summary
    if dry_run:
        return summary

    registry_keys = {r["field_key"] for r in registry_rows}
    spend = SpendTracker(cap_usd=float(spend_cap_usd))
    run_id = await create_run(conn, cohort_id=cohort_id, deployment=chosen_model, spend_cap=spend_cap_usd,
                              planned=len(docs), created_by=created_by)
    summary.run_id = run_id
    try:
        for c in docs:
            msgs = build_inventory_messages(registry_rows, c.terms.text, filer=c.issuer_group,
                                            accession=c.accession_number)
            try:
                res = await _call(chosen_model, msgs, catalog=catalog, spend=spend, max_tokens=max_tokens,
                                  what=f"inventory:{c.accession_number}", kind="inventory",
                                  tags=[f"inventory_run:{run_id}"])
            except SpendCapReached as exc:
                summary.status, summary.stop_reason = "stopped_spend_cap", str(exc)
                break
            facts, accepted, rejected = ({}, [], [])
            if res.status == "ok":
                facts, accepted, rejected = parse_items(res.parsed, c.doc, registry_keys)
            await store_document(conn, run_id, c, res, chosen_model, facts, accepted, rejected)
            summary.documents_done += 1
            summary.items_accepted += len(accepted)
            summary.items_rejected += len(rejected)
            if progress:
                progress(c.accession_number, f"{res.status} +{len(accepted)} items, {len(rejected)} rejected, "
                                             f"${res.cost_usd:.5f} (run ${spend.spent_usd:.4f})")
        else:
            summary.status = "completed"
        agg = await aggregate(conn, run_id, catalog=catalog, deployment=chosen_model, spend=spend,
                              use_model=group_with_model)
        summary.spent_usd = spend.spent_usd
        await finish_run(conn, run_id, status=summary.status, spent_usd=spend.spent_usd,
                         documents_done=summary.documents_done, items_accepted=summary.items_accepted,
                         items_rejected=summary.items_rejected, grouping_method=agg["grouping_method"],
                         stop_reason=summary.stop_reason or agg.get("grouping_note"),
                         finished_at=datetime.now(timezone.utc),
                         report={"plan": summary.plan, "unloadable": unloadable,
                                 "concepts": agg["concepts"], "label_dictionary": agg["label_dictionary"]})
    except Exception as exc:  # noqa: BLE001 — recorded, then re-raised
        summary.status, summary.stop_reason = "failed", f"{type(exc).__name__}: {exc}"[:1500]
        await finish_run(conn, run_id, status="failed", spent_usd=spend.spent_usd,
                         documents_done=summary.documents_done, stop_reason=summary.stop_reason,
                         finished_at=datetime.now(timezone.utc))
        raise
    return summary


# ═══ Aggregation into concepts ═════════════════════════════════════════════
def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (s or "").lower()).strip("_")[:80] or "concept"


def group_labels(items: list[dict], model_groups: list[dict] | None,
                 unique_labels: list[str]) -> list[dict]:
    """Pure. ``items`` carry label_normalized; ``model_groups`` (optional) is
    the model's answer over ``unique_labels`` (by index). Returns concepts:
    {concept_key, display_label, norm_labels, method, maps_to, proposed}.
    Every normalised label ends up in exactly ONE concept."""
    assigned: dict[str, int] = {}
    groups: list[dict] = []
    for g in model_groups or []:
        ids = [i for i in (g.get("label_ids") or []) if isinstance(i, int) and 0 <= i < len(unique_labels)]
        labels = [unique_labels[i] for i in ids if unique_labels[i] not in assigned]
        if not labels:
            continue
        idx = len(groups)
        for lab in labels:
            assigned[lab] = idx
        maps_to = g.get("maps_to") if isinstance(g.get("maps_to"), str) else None
        groups.append({"display_label": str(g.get("concept") or labels[0])[:200], "norm_labels": labels,
                       "method": "model", "maps_to": maps_to,
                       "proposed": g.get("proposed_field_key") if isinstance(g.get("proposed_field_key"), str) else None})
    for lab in unique_labels:
        if lab not in assigned:
            assigned[lab] = len(groups)
            groups.append({"display_label": lab, "norm_labels": [lab], "method": "normalised_label",
                           "maps_to": None, "proposed": None})
    used: Counter = Counter()
    for g in groups:
        key = _slug(g["display_label"])
        used[key] += 1
        g["concept_key"] = key if used[key] == 1 else f"{key}_{used[key]}"
    return groups


def concept_rows(items: list[dict], groups: list[dict], registry_keys: set[str]) -> list[dict]:
    """Per concept: original labels (synonyms), issuers, frequency, documents,
    example values, mapped/proposed field, misleading flags, and the item ids."""
    by_norm: dict[str, list[dict]] = defaultdict(list)
    for it in items:
        by_norm[it["label_normalized"]].append(it)
    out = []
    for g in groups:
        its = [it for lab in g["norm_labels"] for it in by_norm.get(lab, [])]
        if not its:
            continue
        mapped_votes = Counter(it["mapped_field_key"] for it in its if it["mapped_field_key"])
        proposed_votes = Counter(it["proposed_field_key"] for it in its if it["proposed_field_key"])
        mapped = g["maps_to"] if g["maps_to"] in registry_keys else (
            mapped_votes.most_common(1)[0][0] if mapped_votes else None)
        proposed = None
        if mapped is None:
            proposed = g["proposed"] or (proposed_votes.most_common(1)[0][0] if proposed_votes else _slug(g["display_label"]))
        examples = []
        for it in its:
            v = it["value_text"]
            if v and v not in examples:
                examples.append(v)
            if len(examples) >= 5:
                break
        out.append({
            "concept_key": g["concept_key"], "display_label": g["display_label"],
            "labels": sorted({it["label"] for it in its}),
            "issuers": sorted({it["issuer_group"] or "(unknown)" for it in its}),
            "frequency": len(its), "document_count": len({it["document_id"] for it in its}),
            "example_values": examples, "mapped_field_key": mapped, "proposed_field_key": proposed,
            "misleading_flags": [{"label": it["label"], "note": it["misleading_note"],
                                  "issuer_group": it["issuer_group"], "accession_number": it["accession_number"],
                                  "quote": it["quote"][:300]} for it in its if it["misleading_label"]],
            "grouping_method": g["method"], "item_ids": [it["id"] for it in its],
        })
    out.sort(key=lambda c: (-c["frequency"], c["concept_key"]))
    return out


def label_dictionary(concepts: list[dict], items: list[dict]) -> dict:
    """issuer -> original label -> concept_key (for issuer-specific rules)."""
    concept_of = {iid: c["concept_key"] for c in concepts for iid in c["item_ids"]}
    out: dict[str, dict[str, str]] = defaultdict(dict)
    for it in items:
        ck = concept_of.get(it["id"])
        if ck:
            out[it["issuer_group"] or "(unknown)"][it["label"]] = ck
    return {k: dict(sorted(v.items())) for k, v in sorted(out.items())}


async def _load_items(conn, run_id) -> list[dict]:
    async with platform_scope(conn):
        rows = await conn.fetch(
            """SELECT id, document_id, accession_number, issuer_group, label, label_normalized, value_text,
                      quote, mapped_field_key, proposed_field_key, misleading_label, misleading_note
               FROM portfolio.edgar_inventory_items WHERE run_id = $1 ORDER BY created_at, id""", run_id)
    return [{**dict(r), "id": str(r["id"]), "document_id": str(r["document_id"])} for r in rows]


async def _registry_keys(conn) -> set[str]:
    async with platform_scope(conn):
        return {r["field_key"] for r in await conn.fetch("SELECT field_key FROM portfolio.note_terms_field_registry")}


async def aggregate(conn, run_id, *, catalog: dict | None = None, deployment: str | None = None,
                    spend: SpendTracker | None = None, use_model: bool = True) -> dict:
    """Group the run's items into concepts and store them. Re-runnable: the
    run's previous concepts are replaced. The ONE model call (if any) is
    subject to the same spending cap; if it cannot be made or fails, grouping
    falls back to normalised labels alone — recorded as such."""
    items = await _load_items(conn, run_id)
    unique = sorted({it["label_normalized"] for it in items})
    registry_keys = await _registry_keys(conn)
    model_groups, method, note = None, "normalised_label", None
    if use_model and unique and deployment and catalog is not None and spend is not None:
        sample: dict[str, dict] = {}
        for it in items:
            sample.setdefault(it["label_normalized"], it)
        listing = "\n".join(
            f"{i}. {lab} — e.g. {(sample[lab]['value_text'] or '')[:60]!r} — maps_to "
            f"{sample[lab]['mapped_field_key'] or NEW}" for i, lab in enumerate(unique))
        msgs = [{"role": "system", "content": GROUPING_INSTRUCTIONS},
                {"role": "user", "content": f"LABELS:\n{listing}"}]
        try:
            res = await _call(deployment, msgs, catalog=catalog, spend=spend, max_tokens=GROUPING_MAX_TOKENS,
                              what="grouping", kind="grouping", tags=[f"inventory_run:{run_id}"])
            if res.status == "ok" and isinstance(res.parsed.get("groups"), list):
                model_groups, method = res.parsed["groups"], "model"
            else:
                note = f"model grouping unusable ({res.status}: {res.error}); grouped by normalised label only"
        except SpendCapReached as exc:
            note = f"model grouping skipped — {exc}; grouped by normalised label only"
    groups = group_labels(items, model_groups, unique)
    concepts = concept_rows(items, groups, registry_keys)
    async with platform_scope(conn):
        await conn.execute("UPDATE portfolio.edgar_inventory_items SET concept_id = NULL WHERE run_id = $1", run_id)
        await conn.execute("DELETE FROM portfolio.edgar_inventory_concepts WHERE run_id = $1", run_id)
        for c in concepts:
            cid = await conn.fetchval(
                """INSERT INTO portfolio.edgar_inventory_concepts
                       (run_id, concept_key, display_label, labels, issuers, frequency, document_count,
                        example_values, mapped_field_key, proposed_field_key, misleading_flags, grouping_method)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8::jsonb,$9,$10,$11::jsonb,$12) RETURNING id""",
                run_id, c["concept_key"], c["display_label"], c["labels"], c["issuers"], c["frequency"],
                c["document_count"], json.dumps(c["example_values"]), c["mapped_field_key"],
                c["proposed_field_key"], json.dumps(c["misleading_flags"]), c["grouping_method"])
            await conn.execute(
                "UPDATE portfolio.edgar_inventory_items SET concept_id = $1 WHERE id = ANY($2::uuid[])",
                cid, c["item_ids"])
    dictionary = label_dictionary(concepts, items)
    return {"grouping_method": method, "grouping_note": note,
            "concepts": [{k: v for k, v in c.items() if k != "item_ids"} for c in concepts],
            "label_dictionary": dictionary}


# ═══ Reads for the Cohorts tab + the markdown report ═══════════════════════
async def inventory_runs(conn, cohort_id) -> list[dict]:
    async with platform_scope(conn):
        rows = await conn.fetch(
            """SELECT id, status, deployment_name, prompt_version, spend_cap_usd, spent_usd,
                      documents_planned, documents_done, items_accepted, items_rejected,
                      grouping_method, stop_reason, started_at, finished_at
               FROM portfolio.edgar_inventory_runs WHERE cohort_id = $1 ORDER BY started_at DESC""",
            cohort_id)
    return [dict(r) for r in rows]


async def inventory_concepts(conn, run_id) -> list[dict]:
    async with platform_scope(conn):
        rows = await conn.fetch(
            """SELECT id, concept_key, display_label, labels, issuers, frequency, document_count,
                      example_values, mapped_field_key, proposed_field_key, misleading_flags, grouping_method
               FROM portfolio.edgar_inventory_concepts WHERE run_id = $1
               ORDER BY frequency DESC, concept_key""", run_id)
    out = []
    for r in rows:
        d = dict(r)
        for k in ("example_values", "misleading_flags"):
            if isinstance(d.get(k), str):
                d[k] = json.loads(d[k])
        out.append(d)
    return out


async def run_label_dictionary(conn, run_id) -> dict:
    async with platform_scope(conn):
        rows = await conn.fetch(
            """SELECT COALESCE(i.issuer_group, '(unknown)') AS issuer, i.label, c.concept_key
               FROM portfolio.edgar_inventory_items i
               JOIN portfolio.edgar_inventory_concepts c ON c.id = i.concept_id
               WHERE i.run_id = $1 ORDER BY 1, 2""", run_id)
    out: dict[str, dict[str, str]] = defaultdict(dict)
    for r in rows:
        out[r["issuer"]][r["label"]] = r["concept_key"]
    return dict(out)


def render_markdown(run: dict, documents: list[dict], concepts: list[dict], dictionary: dict) -> str:
    def esc(s):
        return str(s if s is not None else "").replace("|", "\\|").replace("\n", " ")
    lines = [
        "# Template Study — inventory of what structured-note filings contain", "",
        f"Inventory run `{run['id']}` over cohort `{run['cohort_id']}` — model `{run['deployment_name']}` "
        f"(prompt {run['prompt_version']}), status **{run['status']}**, spent ${float(run['spent_usd'] or 0):.4f} "
        f"of a ${float(run['spend_cap_usd']):.2f} cap.", "",
        f"Documents read: {run['documents_done']} of {run['documents_planned']}. Items accepted: "
        f"{run['items_accepted']}. Items REJECTED because their quote was not found in the filing: "
        f"{run['items_rejected']}. Concept grouping: {run['grouping_method']}.", "",
        "Generated by `apps/api/scripts/run_edgar_inventory.py --write-doc`; the same data is on the "
        "EDGAR Pipeline page, Cohorts tab.", "",
        "## Documents", "",
        "| Issuer | Accession | Kind | Product family (as described) | Program / product supplement | Payout table | Issue size | Terms tokens | Items | Rejected |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for d in documents:
        lines.append(f"| {esc(d['issuer_group'])} | {esc(d['accession_number'])} | {esc(d['document_kind'])} | "
                     f"{esc(d['product_family'])} | {esc(d['program_supplement'])} | {esc(d['has_payout_table'])} | "
                     f"{esc(d['issue_size'])} | {d['terms_tokens_est']} | {d['items_accepted']} | {d['items_rejected']} |")
    lines += ["", "## Concepts", "",
              "| Concept | Labels (synonyms) | Issuers | Frequency | Docs | Existing field | Proposed new field | Example values | Misleading-label flags |",
              "|---|---|---|---|---|---|---|---|---|"]
    for c in concepts:
        lines.append(f"| {esc(c['display_label'])} | {esc('; '.join(c['labels']))} | {esc(', '.join(c['issuers']))} | "
                     f"{c['frequency']} | {c['document_count']} | {esc(c['mapped_field_key'])} | "
                     f"{esc(c['proposed_field_key'])} | {esc('; '.join(map(str, c['example_values'][:3])))} | "
                     f"{esc('; '.join(f['label'] + ': ' + (f['note'] or '') for f in c['misleading_flags']))} |")
    lines += ["", "## Per-issuer label dictionary", "", "Issuer -> label exactly as written -> concept.", ""]
    for issuer, labels in sorted(dictionary.items()):
        lines.append(f"### {issuer}")
        lines.append("")
        for lab, ck in sorted(labels.items()):
            lines.append(f"- {esc(lab)} -> `{ck}`")
        lines.append("")
    return "\n".join(lines) + "\n"
