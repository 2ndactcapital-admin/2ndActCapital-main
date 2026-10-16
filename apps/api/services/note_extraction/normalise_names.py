"""Names shown NORMALISED on the review screen (bulkfetch Task 10).

Display plus a review flag — never a rewrite. The reviewer's typed value is
what is saved; this module only says which canonical record a name matches and
whether that record is an affiliate of the note's own issuer group:

  * issuer_entity / guarantor_entity -> ``portfolio.structured_note_issuers``
    (matched on filer_name or credit_entity, after ``schema.normalize_name``
    and dropping EDGAR's state suffix such as " /DE/").
  * calculation_agent and each distribution row -> ``portfolio.distribution_
    participants`` (canonical name or alias), the SAME index
    ``participants.match_distribution`` uses.

needs_review: an issuer or guarantor that matches no issuer row, or matches
one whose credit entity is not the note's own issuer group's credit entity.
An unmatched agent is shown as "no match" only — the participants table is
incomplete by design (it grows from the data), so it is not a review reason.
No CRM linkage.
"""
from __future__ import annotations

import re

from services.note_extraction import participants as parts
from services.note_extraction.schema import normalize_name

_STATE_SUFFIX = re.compile(r"\s*/[A-Za-z]{2,3}/?\s*$")
ISSUER_FIELDS = ("issuer_entity", "guarantor_entity")
AGENT_FIELDS = ("calculation_agent",)


def _key(name: str | None) -> str:
    return normalize_name(_STATE_SUFFIX.sub("", name or ""))


async def load_issuer_rows(conn) -> list[dict]:
    rows = await conn.fetch(
        "SELECT filer_cik, issuer_group, filer_name, filer_role, credit_entity FROM portfolio.structured_note_issuers")
    return [dict(r) for r in rows]


def note_issuer_group(issuer_rows: list[dict], filing_cik: str | None) -> str | None:
    cik = str(int(filing_cik)) if filing_cik and str(filing_cik).isdigit() else filing_cik
    for r in issuer_rows:
        if r["filer_cik"] == cik:
            return r["issuer_group"]
    return None


def match_issuer(name: str | None, issuer_rows: list[dict], note_group: str | None) -> dict | None:
    """None for no name; else {matched, canonical, matched_on, issuer_group,
    credit_entity, affiliate, needs_review_reason}."""
    if not name:
        return None
    key = _key(name)
    group_credit = {r["credit_entity"] for r in issuer_rows if note_group and r["issuer_group"] == note_group}
    for on in ("filer_name", "credit_entity"):
        for r in issuer_rows:
            if key and _key(r[on]) == key:
                affiliate = bool(note_group) and r["issuer_group"] == note_group
                same_credit = r["credit_entity"] in group_credit
                return {"matched": True, "canonical": r["filer_name"] if on == "filer_name" else r["credit_entity"],
                        "matched_on": on, "filer_cik": r["filer_cik"], "issuer_group": r["issuer_group"],
                        "credit_entity": r["credit_entity"], "affiliate": affiliate,
                        "needs_review_reason": None if same_credit else (
                            f"{name!r} matches {r['filer_name']} ({r['issuer_group']}, credit "
                            f"{r['credit_entity']}), not this note's issuer group "
                            f"{note_group or '(unknown)'}'s credit entity")}
    return {"matched": False, "canonical": None, "matched_on": None, "issuer_group": None, "credit_entity": None,
            "affiliate": False,
            "needs_review_reason": f"{name!r} matches no row of the structured-note issuer table"}


def match_agent(name: str | None, participant_rows: list[dict], note_group: str | None) -> dict | None:
    if not name:
        return None
    members, _unmatched = parts.match_distribution([{"name": name}], participant_rows)
    m = members[0] if members else {"matched": False}
    if not m.get("matched"):
        return {"matched": False, "canonical": None, "participant_type": None, "affiliated_issuer_group": None,
                "affiliate": False, "needs_review_reason": None}
    group = m.get("affiliated_issuer_group")
    return {"matched": True, "canonical": m["canonical_name"], "participant_type": m.get("participant_type"),
            "affiliated_issuer_group": group,
            "affiliate": bool(note_group) and (group or "").casefold() == note_group.casefold(),
            "needs_review_reason": None}


def normalise_note(values: dict, *, issuer_rows: list[dict], participant_rows: list[dict],
                   note_group: str | None) -> dict:
    """{field_key: match} for the scalar fields, and {"distribution": [match per
    row]} — for whatever value is current (gold if reviewed, else pre-fill)."""
    out: dict = {"note_issuer_group": note_group}
    for k in ISSUER_FIELDS:
        out[k] = match_issuer(values.get(k), issuer_rows, note_group)
    for k in AGENT_FIELDS:
        out[k] = match_agent(values.get(k), participant_rows, note_group)
    dist = values.get("distribution")
    out["distribution"] = [match_agent((m or {}).get("name") if isinstance(m, dict) else None,
                                       participant_rows, note_group)
                           for m in (dist if isinstance(dist, list) else [])]
    return out


def review_reasons(field_key: str, value, *, issuer_rows: list[dict], note_group: str | None) -> list[str]:
    """needs_review reasons for a reviewer's saved value of an issuer field."""
    if field_key not in ISSUER_FIELDS or not value:
        return []
    m = match_issuer(str(value), issuer_rows, note_group)
    return [f"normalisation: {m['needs_review_reason']}"] if m and m["needs_review_reason"] else []
