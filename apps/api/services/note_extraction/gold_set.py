"""The GOLD SET sampler (goldset.structural): plan -> fetch -> propose -> report.

PLAN. A frozen edgar cohort stratified by BANK (the seven largest credit groups
by selected volume) x ERA (four two-year eras) — 28 strata, EQUAL allocation
(``ceil(size / 28)`` per stratum, so the cohort is at least ``size`` when every
stratum is deep enough). Within a stratum the order is Postgres's seeded
``hashtextextended(accession, seed)`` — the same seeded hash edgar_cohorts uses
— so the same seed always gives the same cohort. Only SELECTED 424B2s: a row the
active selection policy chose (``selection_policy_version`` set, status past
'not_selected'). The manifest is keyed by accession, so a member can never
appear twice; the plan de-duplicates anyway. The cohort's definition records
the banks, the eras, the per-stratum count and the seed.

About a quarter of fetched 424B2s are FINAL pricing supplements (the rest are
preliminaries, term sheets and product supplements; document_kind is set at
fetch), which is why the plan over-fetches ~300 to propose ~50.

PROPOSE. From the cohort's FETCHED final pricing supplements only
(document_kind 'pricing_supplement' with a stored document), proposes
``note_gold_candidates``: at least ``MIN_PER_BANK`` per bank, at least one per
era per bank where the pool has one, at least ``traps.TRAP_MINIMUMS[tag]`` per
trap tag where the pool has them, then round-robin to the target. A shortfall
is REPORTED per stratum and per tag — never padded with notes that do not meet
the requirement. A filing already proposed in ANY batch is never proposed
again (also enforced by the table's UNIQUE (reference_filing_id)).

Nothing here calls a model or fetches a document from the SEC; ``propose``
reads the stored filing through documents.load_document (R2), and takes an
injectable ``downloader`` so a verify can supply the bytes.
"""
from __future__ import annotations

import math
import random
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from services import edgar_cohorts
from services.database import platform_scope
from services.note_extraction import gold, traps

BANKS = ("JPMorgan", "Morgan Stanley", "UBS", "Goldman Sachs", "Citigroup", "Bank of America", "Barclays")
ERAS = (("2019-20", 2019, 2020), ("2021-22", 2021, 2022), ("2023-24", 2023, 2024), ("2025-26", 2025, 2026))
FORM_TYPE = "424B2"
FINAL_KIND = "pricing_supplement"
# Manifest statuses a FETCHED final pricing supplement can hold (fetch decided it, or later).
FETCHED_FINAL_STATUSES = ("ready_for_extraction", "prefilter_skipped", "extraction_submitted", "extracted",
                          "needs_review", "extraction_failed")
MIN_PER_BANK = 6
MIN_PER_BANK_ERA = 1
DEFAULT_SEED = 20261008
PLAN_KIND = "gold_plan"


class GoldSetError(ValueError):
    pass


def era_label(year: int | None) -> str | None:
    if year is None:
        return None
    for label, a, b in ERAS:
        if a <= year <= b:
            return label
    return None


def stratum_label(bank: str, era: str) -> str:
    return f"{bank} | {era}"


def strata() -> list[tuple[str, str]]:
    return [(b, e[0]) for b in BANKS for e in ERAS]


def per_stratum_for(size: int) -> int:
    if size < 1:
        raise GoldSetError("--size must be at least 1")
    return math.ceil(size / len(strata()))


def plan_definition(*, size: int, seed: int, name: str) -> dict:
    return {
        "source": PLAN_KIND,
        "purpose": "gold set over-fetch: bank x era, equal allocation",
        "form_type": FORM_TYPE,
        "selection": "selection_policy_version IS NOT NULL AND pipeline_status NOT IN ('discovered','not_selected')",
        "banks": list(BANKS),
        "eras": [{"label": l, "from": a, "to": b} for l, a, b in ERAS],
        "strata": len(strata()),
        "size": size,
        "per_stratum": per_stratum_for(size),
        "seed": seed,
        "name": name,
    }


def _era_case(col: str) -> str:
    return "CASE " + " ".join(
        f"WHEN extract(year FROM {col}) BETWEEN {a} AND {b} THEN '{l}'" for l, a, b in ERAS) + " END"


async def plan_members(conn, *, size: int, seed: int, accessions: list[str] | None = None) -> dict:
    """The stratified member list (nothing written).

    ``accessions`` restricts the candidate pool (a verify's fixture rows, or an
    operator's own short list); None = the whole manifest.

    Returns {"members": [(accession, stratum)], "per_stratum": n,
             "available": {stratum: n}, "shortfall": {stratum: missing}}."""
    per = per_stratum_for(size)
    lo, hi = ERAS[0][1], ERAS[-1][2]
    args: list = [list(BANKS), FORM_TYPE, seed, per]
    extra = ""
    if accessions is not None:
        args.append(list(accessions))
        extra = f" AND f.accession_number = ANY(${len(args)}::text[])"
    sql = f"""
        WITH cand AS (
            SELECT DISTINCT ON (f.accession_number)
                   f.accession_number, i.issuer_group AS bank, {_era_case('f.filing_date')} AS era
              FROM portfolio.edgar_index_filings f
              JOIN portfolio.structured_note_issuers i ON i.filer_cik = f.primary_issuer_cik
             WHERE i.issuer_group = ANY($1::text[])
               AND f.form_type = $2
               AND f.selection_policy_version IS NOT NULL
               AND f.pipeline_status NOT IN ('discovered', 'not_selected')
               AND f.filing_date BETWEEN DATE '{lo}-01-01' AND DATE '{hi}-12-31'{extra}
             ORDER BY f.accession_number
        ),
        ranked AS (
            SELECT accession_number, bank, era,
                   row_number() OVER (PARTITION BY bank, era
                                      ORDER BY hashtextextended(accession_number, $3::bigint), accession_number) AS rk,
                   count(*) OVER (PARTITION BY bank, era) AS avail
              FROM cand
        )
        SELECT accession_number, bank, era, rk, avail FROM ranked WHERE rk <= $4
         ORDER BY bank, era, rk"""
    async with platform_scope(conn):
        await conn.execute("SET LOCAL work_mem = '128MB'")
        rows = await conn.fetch(sql, *args)
    available: dict[str, int] = {stratum_label(b, e): 0 for b, e in strata()}
    members, seen = [], set()
    for r in rows:
        st = stratum_label(r["bank"], r["era"])
        available[st] = int(r["avail"])
        if r["accession_number"] in seen:
            continue
        seen.add(r["accession_number"])
        members.append((r["accession_number"], st))
    # cohort order: stratum by stratum in the fixed (bank, era) order, rank within
    order = {stratum_label(b, e): i for i, (b, e) in enumerate(strata())}
    members.sort(key=lambda m: order[m[1]])
    shortfall = {st: per - min(per, n) for st, n in available.items() if n < per}
    return {"members": members, "per_stratum": per, "available": available, "shortfall": shortfall}


async def create_plan(conn, *, name: str, size: int, seed: int = DEFAULT_SEED, created_by=None,
                      accessions: list[str] | None = None) -> dict:
    """Build and FREEZE the plan cohort. Returns the plan plus the cohort id."""
    plan = await plan_members(conn, size=size, seed=seed, accessions=accessions)
    if not plan["members"]:
        raise GoldSetError("no selected 424B2 filing of the seven banks matches — nothing to plan")
    definition = plan_definition(size=size, seed=seed, name=name)
    if accessions is not None:
        definition["restricted_to_accessions"] = len(accessions)
    cid = await edgar_cohorts._insert_frozen(
        conn, name=edgar_cohorts._check_name(name), purpose="gold set: bank x era over-fetch (goldset)",
        kind="custom", definition=definition, members=plan["members"], created_by=created_by)
    return {**plan, "cohort_id": cid, "definition": definition}


# ═══ Propose ═══════════════════════════════════════════════════════════════
@dataclass
class PoolNote:
    reference_filing_id: str
    accession_number: str
    bank: str
    era: str | None
    filing_year: int | None
    product_type: str = "other"
    trap_tags: list[str] = field(default_factory=list)


async def cohort_final_pool(conn, cohort_id) -> tuple[list[dict], dict]:
    """The cohort's members that are FETCHED final pricing supplements, plus
    counts of what was excluded and why."""
    async with platform_scope(conn):
        rows = await conn.fetch(
            """SELECT m.accession_number, m.stratum, f.pipeline_status, f.document_kind, f.reference_filing_id,
                      f.filing_date, i.issuer_group, rf.r2_key
                 FROM portfolio.edgar_cohort_members m
                 JOIN portfolio.edgar_index_filings f ON f.accession_number = m.accession_number
                 LEFT JOIN portfolio.structured_note_issuers i ON i.filer_cik = f.primary_issuer_cik
                 LEFT JOIN portfolio.reference_filings rf ON rf.id = f.reference_filing_id
                WHERE m.cohort_id = $1
                ORDER BY m.position""", cohort_id)
    excluded: Counter = Counter()
    keep = []
    for r in rows:
        if r["reference_filing_id"] is None or r["r2_key"] is None:
            excluded["not fetched"] += 1
        elif r["document_kind"] != FINAL_KIND:
            excluded[f"document_kind {r['document_kind']}"] += 1
        elif r["pipeline_status"] not in FETCHED_FINAL_STATUSES:
            excluded[f"status {r['pipeline_status']}"] += 1
        else:
            keep.append(dict(r))
    return keep, {"members": len(rows), "eligible": len(keep), "excluded": dict(excluded)}


async def already_proposed(conn) -> dict[str, str]:
    """reference_filing_id -> sample_batch of every existing candidate (any batch)."""
    async with platform_scope(conn):
        rows = await conn.fetch("SELECT reference_filing_id, sample_batch FROM portfolio.note_gold_candidates")
    return {str(r["reference_filing_id"]): r["sample_batch"] for r in rows}


def choose(pool: list[PoolNote], *, target: int, seed: int = DEFAULT_SEED,
           min_per_bank: int = MIN_PER_BANK, min_per_bank_era: int = MIN_PER_BANK_ERA,
           trap_minimums: dict[str, int] | None = None) -> tuple[list[PoolNote], dict]:
    """Pure: pick up to ``target`` notes meeting the minimums where the pool
    allows; return (chosen, shortfalls). Deterministic for a seed.

    Order of requirements: one per (bank, era); each trap tag to its minimum;
    each bank to its minimum; then round-robin over banks (fewest first) to the
    target. Within a requirement the note covering the most still-unmet trap
    tags is preferred, then the seeded order. Never exceeds ``target``."""
    trap_minimums = dict(traps.TRAP_MINIMUMS if trap_minimums is None else trap_minimums)
    rng = random.Random(seed)
    items = sorted(pool, key=lambda n: n.accession_number)
    rng.shuffle(items)
    rank = {n.reference_filing_id: i for i, n in enumerate(items)}
    chosen: dict[str, PoolNote] = {}

    def tag_counts() -> Counter:
        return Counter(t for n in chosen.values() for t in n.trap_tags)

    def unmet_tags() -> set[str]:
        c = tag_counts()
        return {t for t, m in trap_minimums.items() if c[t] < m}

    def pick(cands: list[PoolNote]) -> PoolNote | None:
        cands = [c for c in cands if c.reference_filing_id not in chosen]
        if not cands or len(chosen) >= target:
            return None
        want = unmet_tags()
        best = min(cands, key=lambda c: (-len(want & set(c.trap_tags)), rank[c.reference_filing_id]))
        chosen[best.reference_filing_id] = best
        return best

    def bank_count(b):
        return sum(1 for n in chosen.values() if n.bank == b)

    # 1. one per (bank, era)
    for b in BANKS:
        for e, _a, _z in ERAS:
            for _ in range(min_per_bank_era):
                pick([n for n in items if n.bank == b and n.era == e])
    # 2. trap tags to their minimums (fill from the bank with fewest so far)
    for tag, m in trap_minimums.items():
        while tag_counts()[tag] < m and len(chosen) < target:
            cands = [n for n in items if tag in n.trap_tags and n.reference_filing_id not in chosen]
            if not cands:
                break
            low = min(bank_count(n.bank) for n in cands)
            if pick([n for n in cands if bank_count(n.bank) == low]) is None:
                break
    # 3. each bank to its minimum (spread across its eras)
    for b in BANKS:
        while bank_count(b) < min_per_bank and len(chosen) < target:
            mine = [n for n in items if n.bank == b and n.reference_filing_id not in chosen]
            if not mine:
                break
            era_n = Counter(n.era for n in chosen.values() if n.bank == b)
            low = min(era_n[n.era] for n in mine)
            pick([n for n in mine if era_n[n.era] == low])
    # 4. fill to target, banks with fewest first
    while len(chosen) < target:
        rest = [n for n in items if n.reference_filing_id not in chosen]
        if not rest:
            break
        low = min(bank_count(n.bank) for n in rest)
        pick([n for n in rest if bank_count(n.bank) == low])

    out = sorted(chosen.values(), key=lambda n: (BANKS.index(n.bank) if n.bank in BANKS else 99,
                                                 n.era or "", rank[n.reference_filing_id]))
    return out, shortfalls(out, pool, target=target, min_per_bank=min_per_bank,
                           min_per_bank_era=min_per_bank_era, trap_minimums=trap_minimums)


def shortfalls(chosen: list[PoolNote], pool: list[PoolNote], *, target: int, min_per_bank: int,
               min_per_bank_era: int, trap_minimums: dict[str, int]) -> dict:
    by_bank = Counter(n.bank for n in chosen)
    by_stratum = Counter((n.bank, n.era) for n in chosen)
    pool_stratum = Counter((n.bank, n.era) for n in pool)
    tags = Counter(t for n in chosen for t in n.trap_tags)
    pool_tags = Counter(t for n in pool for t in n.trap_tags)
    return {
        "total": {"target": target, "proposed": len(chosen), "missing": max(0, target - len(chosen))},
        "per_bank": {b: {"need": min_per_bank, "got": by_bank[b], "pool": sum(1 for n in pool if n.bank == b)}
                     for b in BANKS if by_bank[b] < min_per_bank},
        "per_stratum": {stratum_label(b, e): {"need": min_per_bank_era, "got": by_stratum[(b, e)],
                                              "pool": pool_stratum[(b, e)]}
                        for b in BANKS for e, _a, _z in ERAS if by_stratum[(b, e)] < min_per_bank_era},
        "per_trap": {t: {"need": m, "got": tags[t], "pool": pool_tags[t]}
                     for t, m in trap_minimums.items() if tags[t] < m},
    }


async def build_pool(conn, cohort_id, *, downloader=None, progress=None) -> tuple[list[PoolNote], dict]:
    """The proposable pool: the cohort's fetched finals, minus filings already in
    ANY gold batch, each classified (product type, trap tags) from its text."""
    from services.note_extraction import documents

    rows, counts = await cohort_final_pool(conn, cohort_id)
    taken = await already_proposed(conn)
    pool: list[PoolNote] = []
    in_other = []
    unreadable = []
    for r in rows:
        fid = str(r["reference_filing_id"])
        if fid in taken:
            in_other.append({"reference_filing_id": fid, "sample_batch": taken[fid]})
            continue
        try:
            doc = await documents.load_document(conn, fid, downloader=downloader)
        except Exception as exc:  # noqa: BLE001 — one unreadable filing never stops the sampler
            unreadable.append({"reference_filing_id": fid, "error": f"{type(exc).__name__}: {exc}"[:200]})
            continue
        year = r["filing_date"].year if r["filing_date"] else None
        bank = r["issuer_group"] or "(no listed issuer)"
        pool.append(PoolNote(fid, r["accession_number"], bank, era_label(year), year,
                             gold.product_type(doc.text), traps.detect(doc.text)))
        if progress:
            progress(fid)
    counts.update({"already_in_a_gold_batch": len(in_other), "unreadable": len(unreadable),
                   "pool": len(pool), "in_other_batches": in_other[:20], "unreadable_detail": unreadable[:20]})
    return pool, counts


async def write_proposals(conn, chosen: list[PoolNote], batch: str) -> int:
    """Insert the proposals. ON CONFLICT DO NOTHING on reference_filing_id: a
    filing proposed concurrently in another batch is NOT re-proposed. Returns
    the number actually inserted."""
    if not batch or not batch.strip():
        raise GoldSetError("a sample batch name is required")
    inserted = 0
    async with platform_scope(conn):   # one transaction for the whole batch
        for n in chosen:
            new_id = await conn.fetchval(
                """INSERT INTO portfolio.note_gold_candidates
                     (reference_filing_id, sample_batch, issuer_group, filing_year, product_type, trap_tags)
                   VALUES ($1, $2, $3, $4, $5, $6::text[])
                   ON CONFLICT (reference_filing_id) DO NOTHING
                   RETURNING id""",
                n.reference_filing_id, batch.strip(), n.bank, n.filing_year, n.product_type, list(n.trap_tags))
            inserted += int(new_id is not None)
    return inserted


async def propose(conn, cohort_id, *, target: int, batch: str, seed: int = DEFAULT_SEED, write: bool = True,
                  downloader=None, progress=None) -> dict:
    if target < 1:
        raise GoldSetError("--target must be at least 1")
    pool, counts = await build_pool(conn, cohort_id, downloader=downloader, progress=progress)
    chosen, short = choose(pool, target=target, seed=seed)
    inserted = await write_proposals(conn, chosen, batch) if (write and chosen) else 0
    return {"cohort_id": str(cohort_id), "batch": batch, "pool_counts": counts, "chosen": chosen,
            "inserted": inserted, "shortfalls": short}


# ═══ Report ════════════════════════════════════════════════════════════════
async def batch_report(conn, batch: str) -> dict:
    async with platform_scope(conn):
        rows = await conn.fetch(
            """SELECT c.reference_filing_id, c.issuer_group, c.filing_year, c.product_type, c.trap_tags, c.status,
                      (SELECT count(*) FROM portfolio.note_gold_values g
                        WHERE g.reference_filing_id = c.reference_filing_id AND g.valid_to IS NULL) AS gold_fields
                 FROM portfolio.note_gold_candidates c WHERE c.sample_batch = $1""", batch)
    grid = {b: {e: 0 for e, _a, _z in ERAS} for b in BANKS}
    other_banks: Counter = Counter()
    for r in rows:
        e = era_label(r["filing_year"])
        if r["issuer_group"] in grid and e:
            grid[r["issuer_group"]][e] += 1
        else:
            other_banks[f"{r['issuer_group']} | {e}"] += 1
    return {
        "batch": batch,
        "notes": len(rows),
        "bank_x_era": grid,
        "outside_grid": dict(other_banks),
        "trap_tags": {t: sum(1 for r in rows if t in (r["trap_tags"] or [])) for t in traps.DETECTORS},
        "product_types": dict(Counter(r["product_type"] or "other" for r in rows)),
        "status": dict(Counter(r["status"] for r in rows)),
        "gold_fields": sum(int(r["gold_fields"]) for r in rows),
    }
