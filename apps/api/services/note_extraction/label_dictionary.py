"""The per-bank LABEL DICTIONARY — generated from an inventory run, never hand-written.

``build_dictionary(items, registry_rows, ...)`` turns the inventory's own items
(every label each bank used, with the exact quote it was found in) into
``{field_key: {labels, by_issuer}}``. scripts/generate_note_label_dictionary.py
runs it over inventory run aa1c8c7c and writes the versioned file
``label_dictionary_v1.json`` beside this module; services.note_extraction.rules
loads that file. Regenerating is the supported way to learn a new bank's labels.

Not the same thing as services.edgar_inventory.run_label_dictionary, which is
the template study's DISPLAY report (issuer -> every raw label -> concept key,
unfiltered, for docs/TEMPLATE_STUDY.md). This one is keyed by REGISTRY field,
keeps only labels a bank really printed, and is a versioned file the rules read.

WHICH FIELD A CONCEPT BELONGS TO is decided by the registry, in this order:
    1. the concept's key is a LIVE field_key                -> that field
    2. it is a RETIRED key whose replacement_rule is a plain
       rename ('renamed: ...')                              -> replaced_by[0]
    3. it is any other RETIRED key                          -> its own bucket under
                                                               "retired" (decision A's
                                                               initial_valuation_date
                                                               lives here, so its labels
                                                               never become pricing labels)
    4. it is in a live field's former_keys                  -> that field
    otherwise it is counted in ``unmapped_concepts`` and left out.

WHICH LABELS SURVIVE: a label is kept only if, after removing trailing ':'/'*'
and footnote markers, it appears verbatim (case- and whitespace-insensitive) in
the item's own quote — i.e. the bank really printed it. The inventory model's
own annotations ("Price to Public (implied)", "estimated_value_pct") never
appear in a filing and drop out by that test. A label carrying a digit (a value
or date baked into it) or longer than 60 characters is dropped too.
"""
from __future__ import annotations

import json
import pathlib
import re
from collections import defaultdict
from datetime import datetime, timezone

DICTIONARY_VERSION = "notefields.labels.v1"
DICTIONARY_PATH = pathlib.Path(__file__).with_name("label_dictionary_v1.json")
SOURCE_RUN_ID = "aa1c8c7c-239a-4057-9fb3-2c911c9c2bc5"
MAX_LABEL_CHARS = 60

_FOOTNOTE = re.compile(r"\s*\((?:\d|[ivx]+|[a-z])\)\s*", re.IGNORECASE)
_TRAIL = re.compile(r"[\s:*†‡]+$")
_LEAD = re.compile(r"^[\s•■▪\-*]+")


def _squash(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip().lower()


def clean_label(label: str | None, quote: str | None) -> str | None:
    """The label as the bank printed it, or None if it is not a real label."""
    if not label:
        return None
    s = _FOOTNOTE.sub(" ", label)
    s = _TRAIL.sub("", _LEAD.sub("", s)).strip()
    s = re.sub(r"\s+", " ", s)
    if not s or len(s) > MAX_LABEL_CHARS or re.search(r"\d", s) or "_" in s:
        return None
    if _squash(s) not in _squash(quote):
        return None
    return s


def concept_field_map(registry_rows: list[dict]) -> tuple[dict[str, str], dict[str, str]]:
    """({concept_key: live field_key}, {concept_key: retired bucket})."""
    live = {r["field_key"]: r for r in registry_rows if r.get("retired_at") is None}
    retired = {r["field_key"]: r for r in registry_rows if r.get("retired_at") is not None}
    to_live: dict[str, str] = {k: k for k in live}
    to_retired: dict[str, str] = {}
    for k, r in retired.items():
        rule = (r.get("replacement_rule") or "").lower()
        targets = list(r.get("replaced_by") or [])
        if rule.startswith("renamed") and targets and targets[0] in live:
            to_live[k] = targets[0]
        else:
            to_retired[k] = k
    for k, r in live.items():
        for fk in r.get("former_keys") or []:
            if fk not in to_live and fk not in to_retired:
                to_live[fk] = k
    return to_live, to_retired


def build_dictionary(items: list[dict], registry_rows: list[dict], *, source_run_id: str = SOURCE_RUN_ID,
                     generated_at: str | None = None) -> dict:
    """``items``: [{concept_key, issuer_group, label, quote}] — one per inventory
    item (concept_key = the concept's mapped key, else its proposed key, else
    its own key)."""
    to_live, to_retired = concept_field_map(registry_rows)
    fields: dict[str, dict] = defaultdict(lambda: {"labels": {}, "by_issuer": defaultdict(dict)})
    retired: dict[str, dict] = defaultdict(lambda: {"labels": {}, "by_issuer": defaultdict(dict)})
    unmapped: dict[str, int] = defaultdict(int)
    dropped = 0
    for it in items:
        ck = it.get("concept_key")
        if ck in to_live:
            bucket = fields[to_live[ck]]
        elif ck in to_retired:
            bucket = retired[to_retired[ck]]
        else:
            unmapped[ck or "?"] += 1
            continue
        label = clean_label(it.get("label"), it.get("quote"))
        if label is None:
            dropped += 1
            continue
        issuer = it.get("issuer_group") or "unknown"
        bucket["labels"].setdefault(label.lower(), label)
        bucket["by_issuer"][issuer].setdefault(label.lower(), label)

    def finish(d):
        return {k: {"labels": sorted(v["labels"].values(), key=str.lower),
                    "by_issuer": {i: sorted(ls.values(), key=str.lower)
                                  for i, ls in sorted(v["by_issuer"].items())}}
                for k, v in sorted(d.items())}

    return {
        "version": DICTIONARY_VERSION,
        "source_run_id": source_run_id,
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "fields": finish(fields),
        "retired": finish(retired),
        "items_seen": len(items),
        "labels_dropped": dropped,
        "unmapped_concepts": dict(sorted(unmapped.items())),
    }


_CACHE: dict | None = None


def load_dictionary(path: pathlib.Path | None = None) -> dict:
    """The generated file. A missing file is an empty dictionary (rules then
    fall back to the registry's synonyms) — never an invented one."""
    global _CACHE
    if path is None and _CACHE is not None:
        return _CACHE
    p = path or DICTIONARY_PATH
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        d = {"version": None, "fields": {}, "retired": {}}
    if path is None:
        _CACHE = d
    return d


def labels_for(field_key: str, dictionary: dict | None = None, *, retired: bool = False) -> list[str]:
    d = dictionary if dictionary is not None else load_dictionary()
    return list(((d.get("retired" if retired else "fields") or {}).get(field_key) or {}).get("labels") or [])


def issuers_using(field_key: str, label: str, dictionary: dict | None = None) -> list[str]:
    d = dictionary if dictionary is not None else load_dictionary()
    by = ((d.get("fields") or {}).get(field_key) or {}).get("by_issuer") or {}
    return sorted(i for i, ls in by.items() if any(l.lower() == label.lower() for l in ls))
