"""Distribution participants — reference data, like the issuer table.

``portfolio.distribution_participants`` is global: a canonical name, alias
spellings and a type (issuer_affiliate, distribution_platform, dealer,
wealth_manager, other). Super-admin writes only (RLS + the router gate).

NO HARD-CODED LIST. The table is SEEDED FROM THE DATA: ``tally`` counts the
participant names the rules find across the pilot's filings, and
``propose_from_tally`` writes each unseen name as a 'proposed' entry with its
observed count. Its type is a proposal too: 'issuer_affiliate' only when the
name shares its leading word with that filing's own issuer name (Barclays
Capital Inc. in a Barclays Bank PLC filing) — derived per filing, not from a
list — otherwise 'other' for a super-admin to set.

Matching never discards: every name is returned either matched (with the
participant id) or in ``unmatched``.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field

from services.database import platform_scope
from services.note_extraction.schema import normalize_name

PARTICIPANT_TYPES = ("issuer_affiliate", "distribution_platform", "dealer", "wealth_manager", "other")


async def load_participants(conn) -> list[dict]:
    rows = await conn.fetch(
        "SELECT id, canonical_name, participant_type, aliases, status, observed_count "
        "FROM portfolio.distribution_participants WHERE status <> 'retired' ORDER BY canonical_name"
    )
    return [dict(r) for r in rows]


def _index(participants: list[dict]) -> dict[str, dict]:
    idx: dict[str, dict] = {}
    for p in participants:
        for name in [p["canonical_name"], *(p.get("aliases") or [])]:
            key = normalize_name(name)
            if key:
                idx.setdefault(key, p)
    return idx


@dataclass
class MatchResult:
    matched: list[dict] = field(default_factory=list)
    unmatched: list[dict] = field(default_factory=list)


def match_names(names: list[dict | str], participants: list[dict]) -> MatchResult:
    """``names``: strings or {name, ...} dicts. Every input lands in exactly one
    of matched / unmatched — nothing is dropped."""
    idx = _index(participants)
    out = MatchResult()
    seen: set[str] = set()
    for item in names:
        name = item.get("name") if isinstance(item, dict) else item
        if not name:
            continue
        key = normalize_name(name)
        if not key or key in seen:
            continue
        seen.add(key)
        p = idx.get(key)
        base = dict(item) if isinstance(item, dict) else {"name": name}
        if p is None:
            out.unmatched.append(base)
        else:
            base.update({"participant_id": str(p["id"]), "canonical_name": p["canonical_name"],
                         "participant_type": p["participant_type"]})
            out.matched.append(base)
    return out


def tally(observations: list[dict]) -> list[dict]:
    """observations: [{name, issuer_name, filing_year}] -> one row per
    normalised name: the most common spelling, every alias seen, total count,
    filings by issuer and year, and the proposed type."""
    groups: dict[str, dict] = {}
    for o in observations:
        key = normalize_name(o.get("name"))
        if not key:
            continue
        g = groups.setdefault(key, {"spellings": Counter(), "count": 0,
                                    "by_issuer": Counter(), "by_year": Counter(),
                                    "affiliate_votes": 0})
        g["spellings"][o["name"]] += 1
        g["count"] += 1
        issuer = o.get("issuer_name") or ""
        g["by_issuer"][issuer] += 1
        if o.get("filing_year"):
            g["by_year"][int(o["filing_year"])] += 1
        lead = normalize_name(issuer).split(" ")[:1]
        if lead and lead[0] and key.split(" ")[:1] == lead:
            g["affiliate_votes"] += 1
    out = []
    for key, g in groups.items():
        spellings = [s for s, _ in g["spellings"].most_common()]
        out.append({
            "normalized": key,
            "canonical_name": spellings[0],
            "aliases": spellings[1:],
            "count": g["count"],
            "by_issuer": dict(g["by_issuer"]),
            "by_year": {str(k): v for k, v in sorted(g["by_year"].items())},
            "proposed_type": "issuer_affiliate" if g["affiliate_votes"] * 2 >= g["count"] else "other",
        })
    out.sort(key=lambda r: -r["count"])
    return out


async def propose_from_tally(conn, rows: list[dict], *, created_by=None) -> dict:
    """Insert unseen names as 'proposed'; add new spellings to an existing
    entry's aliases and bump its observed_count. Super-admin scope, one
    transaction."""
    inserted = updated = 0
    async with platform_scope(conn):
        existing = await load_participants(conn)
        idx = _index(existing)
        for r in rows:
            hit = idx.get(r["normalized"])
            if hit is None:
                await conn.execute(
                    """INSERT INTO portfolio.distribution_participants
                         (canonical_name, participant_type, aliases, status, observed_count, notes, created_by)
                       VALUES ($1, $2, $3, 'proposed', $4, $5, $6)
                       ON CONFLICT (lower(canonical_name)) DO NOTHING""",
                    r["canonical_name"], r["proposed_type"], r["aliases"], r["count"],
                    "proposed from the rules tally; type to be confirmed by a super admin",
                    created_by,
                )
                inserted += 1
            else:
                new_aliases = sorted(set(hit.get("aliases") or []) | set(r["aliases"])
                                     | ({r["canonical_name"]} - {hit["canonical_name"]}))
                await conn.execute(
                    """UPDATE portfolio.distribution_participants
                          SET aliases = $2, observed_count = observed_count + $3, updated_at = now()
                        WHERE id = $1""",
                    hit["id"], new_aliases, r["count"],
                )
                updated += 1
    return {"inserted": inserted, "updated": updated}


def channel_tally(staged: list[dict]) -> dict:
    """The harness's distribution report: participants by issuer and year with
    the fees they were paid. ``staged``: [{issuer, year, participants: [{name,
    role, fee_type, fee_pct}]}]."""
    by_key: dict[tuple, dict] = defaultdict(lambda: {"notes": 0, "fee_pcts": [], "roles": Counter(),
                                                      "fee_types": Counter()})
    for s in staged:
        for p in s.get("participants") or []:
            k = (normalize_name(p.get("name")), s.get("issuer"), s.get("year"))
            row = by_key[k]
            row["notes"] += 1
            row["roles"][p.get("role")] += 1
            if p.get("fee_type"):
                row["fee_types"][p.get("fee_type")] += 1
            if p.get("fee_pct") is not None:
                row["fee_pcts"].append(float(p["fee_pct"]))
    out = []
    for (name, issuer, year), row in by_key.items():
        fees = row["fee_pcts"]
        out.append({"participant": name, "issuer": issuer, "year": year, "notes": row["notes"],
                    "roles": dict(row["roles"]), "fee_types": dict(row["fee_types"]),
                    "fee_pct_min": min(fees) if fees else None, "fee_pct_max": max(fees) if fees else None,
                    "fee_pct_avg": (sum(fees) / len(fees)) if fees else None})
    out.sort(key=lambda r: (-r["notes"], r["participant"] or ""))
    return {"rows": out}


def match_distribution(members: list[dict], participants: list[dict]) -> tuple[list[dict], list[dict]]:
    """Annotate every member of a resolved ``distribution`` list with the
    participant it matches (by canonical name or alias, after normalisation).
    Returns (members, unmatched). An unmatched member stays IN the list with
    ``participant_id`` None — it is reported, never dropped."""
    idx = _index(participants)
    out, unmatched = [], []
    for m in members or []:
        if not isinstance(m, dict):
            continue
        row = dict(m)
        p = idx.get(normalize_name(m.get("name")))
        if p is None:
            row.update({"participant_id": None, "canonical_name": None, "matched": False})
            unmatched.append({"name": m.get("name"), "role": m.get("role"), "source": "distribution"})
        else:
            row.update({"participant_id": str(p["id"]), "canonical_name": p["canonical_name"],
                        "participant_type": p["participant_type"], "matched": True})
        out.append(row)
    return out, unmatched
