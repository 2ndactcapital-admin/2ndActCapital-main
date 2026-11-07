"""SECMASTER1 — the matcher / upsert into the platform security master (Task 4).

ONE BATCH = ONE TRANSACTION (platform write context: app.is_super_admin LOCAL). A batch is read with a handful of
set-based queries and written with executemany — never a row at a time (securities_global.create_security /
add_identifier stay the per-row Super Admin API; this is the bulk path the jobs and the one-time load use, under
the SAME RLS policy, which is the real gate).

MATCH ORDER, per source record (the first that answers wins):
    1. CUSIP   — a live cusip identifier                                   path 'cusip'
    2. FIGI    — a live figi identifier                                    path 'figi'
       (ISIN    — a live isin identifier, when the source gives one        path 'isin')
    3. ticker + exchange — exactly ONE security holding the ticker whose exchange (any provider's 'exchange'
       attribute) is in the same exchange family                           path 'ticker_exchange'
    4. none    — create a new security (only sources allowed to create, within the batch's max_new)
Merged duplicates are followed to their canonical row (canonical_id), as get_by_identifier does.

NOTHING EXISTING IS OVERWRITTEN. A disagreement becomes ONE open review item (portfolio.security_master_review,
unique per kind/subject/field/incoming value — a re-run adds none):
    ticker_conflict        the ticker is held by a security on another exchange family, or with no exchange at all
                           (the 13 platform INDEX rows: SPX, NDX, RTY …) — nothing is matched or created
    ambiguous_match        more than one security holds the ticker on that exchange family
    ticker_cusip_conflict  same ticker, different CUSIP, and the move rule below does not apply
    identifier_conflict    the matched security already has a different CUSIP / FIGI / ISIN, or the value is held by
                           another security
    type_mismatch          an authoritative source (OpenFIGI, FinanceDatabase ETFs) types it differently
    exchange_mismatch      a source places a security matched by CUSIP / FIGI on another exchange family
    ticker_moved           (informational) the move rule fired
Names are never compared (every source spells them differently) and never rewritten.

THE MOVE RULE (a ticker that moves to a new CUSIP). A record matched by CUSIP or FIGI carries ticker T; T is live
on ANOTHER security O whose own live CUSIP differs from the record's, on a compatible exchange family, and O is not
an index → O's ticker identifier is RETIRED (valid_to = now(), never deleted), T is added to the matched security,
and a 'ticker_moved' review item records it.

ATTRIBUTES are stored per (security, field, PROVIDER) — two sources never overwrite each other; the rank for data
type 'reference' decides the read (read_reference). A changed value is a valid-axis restatement (close + insert);
an equal value writes nothing; first_seen is written once per provider and never restated.

DELISTING (mark_absent). After the WHOLE listed universe (both Nasdaq Trader files) has been processed: a security
the provider lists that is absent from this file gets absent_runs = 1 (and last_seen = the previous file's date);
each later DISTINCT file it is still absent from adds one; at 3 its listing_status becomes 'delisted'. The same
file twice counts once. Present again → absent_runs / last_seen retired, listing_status back to 'listed' (the
batch's own attribute write). Nothing is ever deleted. A file that would newly mark more than ``max_absent_share`` of
the universe absent (and more than ``truncation_floor`` securities) is treated as truncated: the check is skipped and
the run says so.

IDEMPOTENT. A second run over the same records writes nothing: every record matches, every attribute is equal,
first_seen exists, every review item is already open (ON CONFLICT DO NOTHING).
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import date

from services import security_master_symbols as SYM
from services.security_data.providers.reference_common import SourceRecord

LIVE = "valid_to IS NULL AND system_to IS NULL"
REFERENCE_FIELDS = ("exchange", "cik", "sector", "industry", "industry_group", "country", "is_etf", "fund_category",
                    "listing_status", "first_seen", "last_seen",
                    # SECMASTER2 — FinanceDatabase's category_group beside its category; a fund's structure
                    "fund_category_group", "fund_structure")
#: sources that may CREATE a security when nothing matches
CREATORS = {"nasdaq_trader", "sec_tickers", "openfigi", "sec_13f_list", "sec_mutual_funds"}
#: sources whose identifiers are ADDED to a matched / new security (FinanceDatabase's only match)
IDENTIFIER_SOURCES = {"nasdaq_trader": {"ticker"}, "sec_tickers": {"ticker"},
                      "openfigi": {"ticker", "figi", "cusip", "isin"},
                      "sec_13f_list": {"ticker", "figi", "cusip", "isin"}, "finance_database": set(),
                      "sec_mutual_funds": {"ticker", "sec_series_id", "sec_class_id"}}
#: sources whose security type is a fact rather than a guess (a disagreement is reviewed)
TYPE_AUTHORITIES = {"openfigi", "sec_13f_list", "finance_database", "sec_mutual_funds"}
#: SECMASTER2 — identifiers beyond cusip / figi / isin / ticker (SourceRecord.extra_ids). A UNIQUE one (a share
#: class) is matched on, after CUSIP / FIGI / ISIN and before the ticker; a SHARED one (a series has many classes)
#: is added, never matched on, and another security holding it is not a conflict.
EXTRA_ID_UNIQUE = ("sec_class_id",)
EXTRA_ID_SHARED = ("sec_series_id",)
#: sources whose 'exchange' token is written only on a security they CREATE (the SEC mutual-fund file states no
#: listing: 'US' on a new class lets a later run match it again; an existing ETF keeps its listing sources' exchange)
EXCHANGE_ON_CREATE_ONLY = {"sec_mutual_funds": "US"}
#: sources that create only when the record says it may (sec_mutual_funds: a mutual-fund ticker, fund_structure set)
CREATE_ONLY_IF = {"sec_mutual_funds": ("fund_structure",
                                       "not a mutual-fund ticker (no fifth-letter X) and not in the master — an "
                                       "exchange-traded class is created by the listing sources")}
DELIST_AFTER = 3
REVIEW_KINDS = ("ticker_conflict", "ticker_cusip_conflict", "identifier_conflict", "type_mismatch",
                "exchange_mismatch", "ambiguous_match", "ticker_moved", "13f_deleted", "13f_conflict")
REVIEW_LABELS = {
    "ticker_conflict": "Ticker held by a security on another exchange (or with none)",
    "ticker_cusip_conflict": "Same ticker, different CUSIP",
    "identifier_conflict": "Identifier disagrees with the existing security",
    "type_mismatch": "Security type disagrees",
    "exchange_mismatch": "Exchange disagrees",
    "ambiguous_match": "More than one security matches",
    "ticker_moved": "Ticker moved to a new CUSIP (old identifier retired)",
    "13f_deleted": "Deleted from the 13(f) list",
    "13f_conflict": "13(f) CUSIP conflicts with the master",
}


@dataclass
class BatchResult:
    records: int = 0
    new: int = 0
    matched: dict = field(default_factory=dict)
    conflicts: int = 0
    unmapped: int = 0
    deferred: int = 0
    unmapped_reasons: dict = field(default_factory=dict)
    identifiers_added: int = 0
    identifiers_retired: int = 0
    attributes_written: int = 0
    review_items: list = field(default_factory=list)
    seen: set = field(default_factory=set)
    by_record: dict = field(default_factory=dict)     # record subject -> (outcome, gsid | None, why)

    def merge(self, o: "BatchResult") -> "BatchResult":
        self.records += o.records
        self.new += o.new
        for k, v in o.matched.items():
            self.matched[k] = self.matched.get(k, 0) + v
        self.conflicts += o.conflicts
        self.unmapped += o.unmapped
        self.deferred += o.deferred
        for k, v in o.unmapped_reasons.items():
            self.unmapped_reasons[k] = self.unmapped_reasons.get(k, 0) + v
        self.identifiers_added += o.identifiers_added
        self.identifiers_retired += o.identifiers_retired
        self.attributes_written += o.attributes_written
        self.review_items += o.review_items
        self.seen |= o.seen
        # SECMASTER1 FIX 1: the per-record outcomes were dropped here, so sync_13f (which merges per batch and
        # then reads total.by_record) saw every OpenFIGI-mapped CUSIP as 'no match' — while the matcher HAD
        # created / matched its security.
        self.by_record.update(o.by_record)
        return self

    @property
    def writes(self) -> int:
        return self.new + self.identifiers_added + self.identifiers_retired + self.attributes_written + len(
            self.review_items)

    def summary(self) -> dict:
        return {"records": self.records, "new": self.new, "matched": dict(self.matched),
                "matched_total": sum(self.matched.values()), "conflicts": self.conflicts, "unmapped": self.unmapped,
                "unmapped_reasons": dict(self.unmapped_reasons), "deferred": self.deferred,
                "identifiers_added": self.identifiers_added, "identifiers_retired": self.identifiers_retired,
                "attributes_written": self.attributes_written, "review_items_created": len(self.review_items),
                "review_item_ids": [str(x) for x in self.review_items[:50]]}


class _Plan:
    """Everything one batch will write, collected first, written in bulk at the end."""

    def __init__(self) -> None:
        self.securities: list[tuple] = []
        self.identifiers: list[tuple] = []
        self.retire_identifier_ids: list = []
        self.attr_close_ids: list = []
        self.attr_inserts: list[tuple] = []
        self.reviews: list[dict] = []


async def _platform(conn) -> None:
    await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")


def _review(plan: _Plan, kind: str, subject: str, provider_key: str, *, gsid=None, field_: str = "",
            incoming: str = "", existing: str | None = None, detail: dict | None = None) -> None:
    plan.reviews.append({"kind": kind, "subject_key": subject[:300], "field": field_, "incoming_value": incoming or "",
                         "existing_value": existing, "provider_key": provider_key,
                         "global_security_id": gsid, "detail": detail or {}})


async def _lookup(conn, records: list[SourceRecord]) -> dict:
    """Live identifiers for every value in the batch → {(id_type, value): [(identifier id, canonical gsid, type,
    raw gsid)]}; exchange attributes and identifiers of every security touched."""
    vals = {"cusip": set(), "figi": set(), "ticker": set(), "isin": set(),
            **{t: set() for t in EXTRA_ID_UNIQUE + EXTRA_ID_SHARED}}
    for r in records:
        for t, v in (r.extra_ids or {}).items():
            if v and t in vals:
                vals[t].add(v)
        if r.cusip:
            vals["cusip"].add(r.cusip)
            isin = SYM.isin_from_cusip(r.cusip)
            if isin:
                vals["isin"].add(isin)
        if r.figi:
            vals["figi"].add(r.figi)
        if r.ticker:
            vals["ticker"].add(r.ticker)
        if r.isin:
            vals["isin"].add(r.isin)
    types, values = [], []
    for t, vs in vals.items():
        for v in vs:
            types.append(t)
            values.append(v)
    rows = await conn.fetch(
        "SELECT i.id, i.id_type, i.id_value, i.global_security_id AS raw, COALESCE(s.canonical_id, s.id) AS gsid "
        "FROM unnest($1::text[], $2::text[]) AS w(t, v) "
        "JOIN portfolio.securities_global_identifiers i ON i.id_type = w.t AND i.id_value = w.v "
        "AND i.valid_to IS NULL AND i.system_to IS NULL "
        "JOIN portfolio.securities_global s ON s.id = i.global_security_id", types, values) if values else []
    idx: dict = {}
    gsids = set()
    for r in rows:
        idx.setdefault((r["id_type"], r["id_value"]), []).append(r)
        gsids.add(r["gsid"])
    return {"idx": idx, "gsids": gsids}


async def _security_facts(conn, gsids, provider_key: str) -> dict:
    """For each security: type, its live identifiers by type, exchange values (any provider), and THIS provider's
    live attributes (id, value)."""
    gl = list(gsids)
    out = {g: {"type": None, "ids": {}, "exchanges": set(), "attrs": {}} for g in gl}
    if not gl:
        return out
    for r in await conn.fetch("SELECT id, security_type FROM portfolio.securities_global WHERE id = ANY($1::uuid[])",
                              gl):
        out[r["id"]]["type"] = r["security_type"]
    for r in await conn.fetch(
            "SELECT i.id, i.id_type, i.id_value, COALESCE(s.canonical_id, s.id) AS gsid "
            "FROM portfolio.securities_global_identifiers i JOIN portfolio.securities_global s "
            "ON s.id = i.global_security_id WHERE COALESCE(s.canonical_id, s.id) = ANY($1::uuid[]) "
            "AND i.valid_to IS NULL AND i.system_to IS NULL", gl):
        out[r["gsid"]]["ids"].setdefault(r["id_type"], []).append((r["id"], r["id_value"]))
    for r in await conn.fetch(
            "SELECT global_security_id, field, value, provider_key, id FROM portfolio.security_reference_attributes "
            "WHERE global_security_id = ANY($1::uuid[]) AND (field = 'exchange' OR provider_key = $2) AND " + LIVE,
            gl, provider_key):
        o = out[r["global_security_id"]]
        if r["field"] == "exchange":
            o["exchanges"].add(r["value"])
        if r["provider_key"] == provider_key:
            o["attrs"][r["field"]] = (r["id"], r["value"])
    return out


def _first(idx, t, v):
    rows = idx.get((t, v)) if v else None
    return rows[0] if rows else None


class Matcher:
    """``await Matcher(provider_key, as_of=…).process(pool, records)`` → BatchResult. One transaction."""

    def __init__(self, provider_key: str, *, as_of: date, run_id=None, create_new: bool | None = None,
                 max_new: int | None = None):
        self.key = provider_key
        self.as_of = as_of
        self.run_id = run_id
        self.create_new = (provider_key in CREATORS) if create_new is None else create_new
        self.max_new = max_new
        self.created = 0

    async def process(self, pool, records: list[SourceRecord]) -> BatchResult:
        async with pool.acquire() as conn:
            async with conn.transaction():
                await _platform(conn)
                return await self.process_conn(conn, records)

    async def process_conn(self, conn, records: list[SourceRecord]) -> BatchResult:
        res = BatchResult()
        plan = _Plan()
        # one record per subject within the batch (the first wins; the rest are the same statement again)
        uniq: dict = {}
        for r in records:
            uniq.setdefault((r.cusip, r.figi, r.ticker, SYM.family(r.exchange)), r)
        records = list(uniq.values())
        res.records = len(records)
        look = await _lookup(conn, records)
        idx = look["idx"]
        facts = await _security_facts(conn, look["gsids"], self.key)
        id_types_allowed = IDENTIFIER_SOURCES.get(self.key, set())

        def owners(t, v):
            return {r["gsid"] for r in idx.get((t, v), [])} if v else set()

        for rec in records:
            subj = rec.subject
            gsid, path = None, None
            for t, v, p in (("cusip", rec.cusip, "cusip"), ("figi", rec.figi, "figi"), ("isin", rec.isin, "isin"),
                            *((t, (rec.extra_ids or {}).get(t), t) for t in EXTRA_ID_UNIQUE)):
                hit = _first(idx, t, v)
                if hit is not None:
                    gsid, path = hit["gsid"], p
                    break
            conflict = False
            if gsid is None and rec.ticker:
                cands = owners("ticker", rec.ticker)
                if cands:
                    compat = [g for g in cands
                              if any(SYM.families_compatible(rec.exchange, ex) for ex in facts[g]["exchanges"])]
                    if len(compat) == 1 and rec.cusip and {x[1] for x in facts[compat[0]]["ids"].get("cusip", [])} \
                            and rec.cusip not in {x[1] for x in facts[compat[0]]["ids"].get("cusip", [])}:
                        # the ticker's holder carries a DIFFERENT CUSIP: a ticker moving to a new CUSIP, not a match
                        if self.create_new and rec.provider_key in CREATORS and facts[compat[0]]["type"] != "index":
                            if self.max_new is not None and self.created >= self.max_new:
                                res.deferred += 1
                                res.by_record[subj] = ("deferred", None, "max new per run reached — resumes next run")
                                continue
                            new_id = self._create(rec, facts, idx, owners, plan, res, id_types_allowed,
                                                  with_ticker=False)
                            self._maybe_move(rec, new_id, {compat[0]}, facts, idx, plan, res)
                            continue
                        _review(plan, "ticker_cusip_conflict", f"ticker:{rec.ticker}", self.key, gsid=compat[0],
                                field_="ticker", incoming=f"{rec.ticker}@{rec.cusip}",
                                existing=", ".join(sorted(x[1] for x in facts[compat[0]]["ids"].get("cusip", []))),
                                detail={"name": rec.name})
                        conflict = True
                    elif len(compat) == 1:
                        gsid, path = compat[0], "ticker_exchange"
                    elif len(compat) > 1:
                        _review(plan, "ambiguous_match", f"ticker:{rec.ticker}", self.key, field_="ticker",
                                incoming=f"{rec.ticker}@{rec.exchange or '?'}",
                                detail={"candidates": [str(g) for g in compat], "name": rec.name})
                        conflict = True
                    elif rec.exchange is None:
                        res.unmapped += 1
                        why = "no exchange to match the ticker on"
                        res.unmapped_reasons[why] = res.unmapped_reasons.get(why, 0) + 1
                        res.by_record[subj] = ("unmapped", None, why)
                        continue
                    else:
                        g0 = sorted(cands, key=str)[0]
                        _review(plan, "ticker_conflict", f"ticker:{rec.ticker}", self.key, gsid=g0, field_="ticker",
                                incoming=f"{rec.ticker}@{rec.exchange}",
                                existing=", ".join(sorted(x for g in cands for x in facts[g]["exchanges"])) or
                                f"no exchange ({facts[g0]['type']})",
                                detail={"holders": [str(g) for g in sorted(cands, key=str)], "name": rec.name,
                                        "holder_types": sorted({str(facts[g]['type']) for g in cands})})
                        conflict = True
            if conflict:
                res.conflicts += 1
                res.by_record[subj] = ("conflict", None, "review")
                continue
            if gsid is not None:
                self._matched(rec, gsid, path, facts, idx, owners, plan, res, id_types_allowed)
                continue
            # 4. none
            if not self.create_new or rec.provider_key not in CREATORS:
                why = "no matching security (this source never creates one)"
                res.unmapped += 1
                res.unmapped_reasons[why] = res.unmapped_reasons.get(why, 0) + 1
                res.by_record[subj] = ("unmapped", None, why)
                continue
            guard = CREATE_ONLY_IF.get(self.key)
            if guard is not None and not (rec.attrs or {}).get(guard[0]):
                res.unmapped += 1
                res.unmapped_reasons[guard[1]] = res.unmapped_reasons.get(guard[1], 0) + 1
                res.by_record[subj] = ("unmapped", None, guard[1])
                continue
            if not (rec.ticker or rec.cusip) or (self.key == "sec_tickers" and not rec.exchange):
                why = "nothing to create it from (no exchange)" if rec.ticker else "no ticker or CUSIP"
                res.unmapped += 1
                res.unmapped_reasons[why] = res.unmapped_reasons.get(why, 0) + 1
                res.by_record[subj] = ("unmapped", None, why)
                continue
            if self.max_new is not None and self.created >= self.max_new:
                res.deferred += 1
                res.by_record[subj] = ("deferred", None, "max new per run reached — resumes next run")
                continue
            self._create(rec, facts, idx, owners, plan, res, id_types_allowed)

        await self._write(conn, plan, res)
        return res

    # ── matched ─────────────────────────────────────────────────────────────
    def _matched(self, rec, gsid, path, facts, idx, owners, plan, res, id_types_allowed):
        f = facts.setdefault(gsid, {"type": None, "ids": {}, "exchanges": set(), "attrs": {}})
        subj = rec.subject
        res.matched[path] = res.matched.get(path, 0) + 1
        res.seen.add(gsid)
        res.by_record[subj] = ("matched", gsid, path)
        have = f["ids"]
        # CUSIP / FIGI / ISIN: add when absent and free; a different live value is a conflict, never replaced
        derived = SYM.isin_from_cusip(rec.cusip) if rec.cusip else None
        for t, v in (("cusip", rec.cusip), ("figi", rec.figi), ("isin", rec.isin or derived)):
            if not v:
                continue
            mine = {x[1] for x in have.get(t, [])}
            if v in mine:
                continue
            if mine:
                if t == "isin" and v == derived and not rec.isin:
                    continue    # a derived ISIN never argues with a stated one
                _review(plan, "identifier_conflict", f"{t}:{v}", self.key, gsid=gsid, field_=t, incoming=v,
                        existing=", ".join(sorted(mine)), detail={"path": path, "ticker": rec.ticker})
                continue
            others = owners(t, v) - {gsid}
            if others:
                _review(plan, "identifier_conflict", f"{t}:{v}", self.key, gsid=gsid, field_=t, incoming=v,
                        existing=f"held by {sorted(str(o) for o in others)[0]}", detail={"path": path})
                continue
            if t in id_types_allowed:
                self._add_identifier(plan, gsid, t, v, primary=False, facts=facts, idx=idx, res=res)
        self._extra_ids(rec, gsid, have, owners, plan, res, id_types_allowed, facts, idx, path=path)
        # the ticker
        if rec.ticker and "ticker" in id_types_allowed:
            tick_owners = owners("ticker", rec.ticker)
            mine = {x[1] for x in have.get("ticker", [])}
            if rec.ticker not in mine:
                others = tick_owners - {gsid}
                if not others and not mine:
                    self._add_identifier(plan, gsid, "ticker", rec.ticker, primary=not have.get("cusip"), facts=facts,
                                         idx=idx, res=res)
                elif others and path in ("cusip", "figi") and not mine:
                    self._maybe_move(rec, gsid, others, facts, idx, plan, res)
                elif others:
                    _review(plan, "ticker_cusip_conflict", f"ticker:{rec.ticker}", self.key, gsid=gsid,
                            field_="ticker", incoming=f"{rec.ticker}@{rec.cusip or rec.figi or '?'}",
                            existing=f"held by {sorted(str(o) for o in others)[0]}", detail={"path": path})
                else:
                    _review(plan, "identifier_conflict", f"ticker:{rec.ticker}", self.key, gsid=gsid, field_="ticker",
                            incoming=rec.ticker, existing=", ".join(sorted(mine)), detail={"path": path})
        # type
        if rec.security_type and self.key in TYPE_AUTHORITIES and f["type"] and rec.security_type != f["type"]:
            _review(plan, "type_mismatch", f"security:{gsid}", self.key, gsid=gsid, field_="security_type",
                    incoming=rec.security_type, existing=f["type"], detail={"ticker": rec.ticker, "path": path})
        # exchange family (only when the match did not already require it)
        if rec.exchange and path in ("cusip", "figi", "isin") and f["exchanges"] and not any(
                SYM.families_compatible(rec.exchange, ex) for ex in f["exchanges"]):
            _review(plan, "exchange_mismatch", f"security:{gsid}", self.key, gsid=gsid, field_="exchange",
                    incoming=rec.exchange, existing=", ".join(sorted(f["exchanges"])), detail={"ticker": rec.ticker})
        self._attrs(plan, res, gsid, rec, f)

    def _maybe_move(self, rec, gsid, others, facts, idx, plan, res):
        """The move rule (module docstring). Exactly one other holder, with a DIFFERENT live CUSIP, on a compatible
        exchange family, not an index."""
        if len(others) == 1:
            o = next(iter(others))
            fo = facts.get(o) or {"type": None, "ids": {}, "exchanges": set()}
            o_cusips = {x[1] for x in fo["ids"].get("cusip", [])}
            compatible = not fo["exchanges"] or any(SYM.families_compatible(rec.exchange or "US", ex)
                                                   for ex in fo["exchanges"])
            if (fo["type"] != "index" and rec.cusip and o_cusips and rec.cusip not in o_cusips and compatible):
                for ident_id, val in fo["ids"].get("ticker", []):
                    if val == rec.ticker:
                        plan.retire_identifier_ids.append(ident_id)
                        res.identifiers_retired += 1
                fo["ids"]["ticker"] = [x for x in fo["ids"].get("ticker", []) if x[1] != rec.ticker]
                idx[("ticker", rec.ticker)] = [r for r in idx.get(("ticker", rec.ticker), []) if r["gsid"] != o]
                self._add_identifier(plan, gsid, "ticker", rec.ticker, primary=False, facts=facts, idx=idx, res=res)
                _review(plan, "ticker_moved", f"ticker:{rec.ticker}", self.key, gsid=gsid, field_="ticker",
                        incoming=f"{rec.ticker}@{rec.cusip}", existing=f"{rec.ticker}@{sorted(o_cusips)[0]}",
                        detail={"from_security": str(o), "to_security": str(gsid), "retired": True})
                return
        _review(plan, "ticker_cusip_conflict", f"ticker:{rec.ticker}", self.key, gsid=gsid, field_="ticker",
                incoming=f"{rec.ticker}@{rec.cusip or rec.figi or '?'}",
                existing=f"held by {sorted(str(o) for o in others)[0]}", detail={})

    # ── new ─────────────────────────────────────────────────────────────────
    def _create(self, rec, facts, idx, owners, plan, res, id_types_allowed, *, with_ticker: bool = True):
        gsid = uuid.uuid4()
        name = (rec.name or rec.ticker or rec.cusip or "").strip()[:500]
        plan.securities.append((gsid, name, rec.ticker, rec.security_type or "equity", "USD"))
        facts[gsid] = {"type": rec.security_type or "equity", "ids": {}, "exchanges": set(), "attrs": {}}
        self.created += 1
        res.new += 1
        res.seen.add(gsid)
        res.by_record[rec.subject] = ("new", gsid, "created")
        derived = SYM.isin_from_cusip(rec.cusip) if rec.cusip else None
        for t, v in (("cusip", rec.cusip), ("isin", rec.isin or derived), ("figi", rec.figi),
                     ("ticker", rec.ticker if with_ticker else None)):
            if not v or (t not in id_types_allowed and t != "ticker"):
                continue
            if owners(t, v) and t != "ticker":
                _review(plan, "identifier_conflict", f"{t}:{v}", self.key, gsid=gsid, field_=t, incoming=v,
                        existing=f"held by {sorted(str(o) for o in owners(t, v))[0]}", detail={"new_security": True})
                continue
            primary = (t == "cusip") or (t == "ticker" and not rec.cusip)
            self._add_identifier(plan, gsid, t, v, primary=primary, facts=facts, idx=idx, res=res)
        self._extra_ids(rec, gsid, facts[gsid]["ids"], owners, plan, res, id_types_allowed, facts, idx, path="new")
        ex = EXCHANGE_ON_CREATE_ONLY.get(self.key)
        self._attrs(plan, res, gsid, rec, facts[gsid], extra={"exchange": ex} if ex else None)
        return gsid

    # ── helpers ─────────────────────────────────────────────────────────────
    def _extra_ids(self, rec, gsid, have, owners, plan, res, id_types_allowed, facts, idx, *, path):
        """SECMASTER2 — add the record's extra identifiers (SEC series / class): absent → added; the same value →
        nothing; a DIFFERENT live value of that type, or a unique value held by another security → review, never
        replaced."""
        for t, v in sorted((rec.extra_ids or {}).items()):
            if not v or t not in id_types_allowed:
                continue
            mine = {x[1] for x in have.get(t, [])}
            if v in mine:
                continue
            if mine:
                _review(plan, "identifier_conflict", f"{t}:{v}", self.key, gsid=gsid, field_=t, incoming=v,
                        existing=", ".join(sorted(mine)), detail={"path": path, "ticker": rec.ticker})
                continue
            others = owners(t, v) - {gsid}
            if others and t not in EXTRA_ID_SHARED:
                _review(plan, "identifier_conflict", f"{t}:{v}", self.key, gsid=gsid, field_=t, incoming=v,
                        existing=f"held by {sorted(str(o) for o in others)[0]}", detail={"path": path,
                                                                                        "ticker": rec.ticker})
                continue
            self._add_identifier(plan, gsid, t, v, primary=False, facts=facts, idx=idx, res=res)

    def _add_identifier(self, plan, gsid, t, v, *, primary, facts, idx, res):
        plan.identifiers.append((gsid, t, v, bool(primary)))
        facts.setdefault(gsid, {"type": None, "ids": {}, "exchanges": set(), "attrs": {}})["ids"].setdefault(
            t, []).append((None, v))
        idx.setdefault((t, v), []).append({"gsid": gsid, "id": None})
        res.identifiers_added += 1

    def _attrs(self, plan, res, gsid, rec, f, extra: dict | None = None):
        attrs = {**(rec.attrs or {}), **(extra or {})}
        if "first_seen" not in f["attrs"]:
            attrs["first_seen"] = self.as_of.isoformat()
        if rec.exchange:
            f["exchanges"].add(rec.exchange)
        for k, v in attrs.items():
            if k not in REFERENCE_FIELDS or v is None or str(v).strip() == "":
                continue
            v = str(v).strip()[:500]
            cur = f["attrs"].get(k)
            if cur is not None and cur[1] == v:
                continue
            if cur is not None and k == "first_seen":
                continue
            if cur is not None and cur[0] is not None:
                plan.attr_close_ids.append(cur[0])
            plan.attr_inserts.append((gsid, k, v, self.key, self.as_of, self.run_id))
            f["attrs"][k] = (None, v)
            res.attributes_written += 1

    async def _write(self, conn, plan: _Plan, res: BatchResult) -> None:
        if plan.securities:
            await conn.executemany(
                "INSERT INTO portfolio.securities_global (id, canonical_id, name, short_name, security_type, "
                "currency_code) VALUES ($1::uuid, $1::uuid, $2, $3, $4, $5)", plan.securities)
        if plan.retire_identifier_ids:
            await conn.execute("UPDATE portfolio.securities_global_identifiers SET valid_to = now() "
                               "WHERE id = ANY($1::uuid[]) AND valid_to IS NULL", plan.retire_identifier_ids)
        if plan.identifiers:
            await conn.executemany(
                "INSERT INTO portfolio.securities_global_identifiers (global_security_id, id_type, id_value, "
                "is_primary) VALUES ($1::uuid, $2, $3, $4)", plan.identifiers)
        if plan.attr_close_ids:
            await conn.execute("UPDATE portfolio.security_reference_attributes SET valid_to = now() "
                               "WHERE id = ANY($1::uuid[]) AND valid_to IS NULL", plan.attr_close_ids)
        if plan.attr_inserts:
            await conn.executemany(
                "INSERT INTO portfolio.security_reference_attributes (global_security_id, field, value, provider_key, "
                "as_of, run_id) VALUES ($1::uuid, $2, $3, $4, $5, $6::uuid)", plan.attr_inserts)
        for rv in plan.reviews:
            rid = await insert_review(conn, run_id=self.run_id, **rv)
            if rid is not None:
                res.review_items.append(rid)


async def insert_review(conn, *, kind: str, subject_key: str, provider_key: str, field: str = "",
                        incoming_value: str = "", existing_value: str | None = None, global_security_id=None,
                        detail: dict | None = None, run_id=None):
    """One OPEN item per (kind, subject, field, incoming value); an identical open item → None (nothing written)."""
    if kind not in REVIEW_KINDS:
        raise ValueError(f"unknown review kind {kind!r}")
    return await conn.fetchval(
        "INSERT INTO portfolio.security_master_review (kind, subject_key, field, incoming_value, existing_value, "
        "provider_key, global_security_id, detail, run_id) VALUES ($1, $2, $3, $4, $5, $6, $7::uuid, $8::jsonb, "
        "$9::uuid) ON CONFLICT (kind, subject_key, field, incoming_value) WHERE status = 'open' AND valid_to IS NULL "
        "AND system_to IS NULL DO NOTHING RETURNING id",
        kind, subject_key, field or "", incoming_value or "", existing_value, provider_key,
        str(global_security_id) if global_security_id else None, json.dumps(detail or {}, default=str), run_id)


# ── delisting ───────────────────────────────────────────────────────────────
@dataclass
class AbsenceResult:
    universe: int = 0
    seen: int = 0
    newly_absent: int = 0
    still_absent: int = 0
    delisted: int = 0
    returned: int = 0
    skipped: str | None = None
    writes: int = 0

    def summary(self) -> dict:
        return dict(self.__dict__)


async def mark_absent(pool, provider_key: str, seen: set, *, file_as_of: date, previous_as_of: date | None,
                      run_id=None, max_absent_share: float = 0.2, truncation_floor: int = 25,
                      scope_ticker_prefix: str | None = None) -> AbsenceResult:
    """See the module docstring. ``seen`` = every security the WHOLE file matched or created.
    ``scope_ticker_prefix`` (verify only; production passes None) limits the universe to securities holding a live
    ticker with that prefix, so a fixture file can prove the rule without touching a real listing."""
    out = AbsenceResult(seen=len(seen))
    async with pool.acquire() as conn:
        async with conn.transaction():
            await _platform(conn)
            listed = {r["global_security_id"]: r for r in await conn.fetch(
                "SELECT id, global_security_id, value, as_of FROM portfolio.security_reference_attributes "
                "WHERE provider_key = $1 AND field = 'listing_status' AND " + LIVE, provider_key)}
            absent = {r["global_security_id"]: r for r in await conn.fetch(
                "SELECT id, global_security_id, value, as_of FROM portfolio.security_reference_attributes "
                "WHERE provider_key = $1 AND field = 'absent_runs' AND " + LIVE, provider_key)}
            last_seen = {r["global_security_id"]: r for r in await conn.fetch(
                "SELECT id, global_security_id FROM portfolio.security_reference_attributes "
                "WHERE provider_key = $1 AND field = 'last_seen' AND " + LIVE, provider_key)}
            if scope_ticker_prefix:
                scoped = {r["global_security_id"] for r in await conn.fetch(
                    "SELECT DISTINCT global_security_id FROM portfolio.securities_global_identifiers WHERE id_type = "
                    "'ticker' AND id_value LIKE $1 AND " + LIVE, scope_ticker_prefix + "%")}
                listed = {g: r for g, r in listed.items() if g in scoped}
                absent = {g: r for g, r in absent.items() if g in scoped}
            out.universe = len(listed)
            missing = [g for g in listed if g not in seen]
            live_listed = [g for g in listed if listed[g]["value"] == "listed"]
            newly_missing = [g for g in missing if listed[g]["value"] == "listed" and g not in absent]
            # a truncated download must not start delisting the market: more than max_absent_share of the listed
            # universe going missing AT ONCE (and more than truncation_floor securities) skips the check
            if live_listed and len(newly_missing) > truncation_floor and len(newly_missing) > max_absent_share * len(
                    live_listed):
                out.skipped = (f"{len(missing)} of {len(listed)} listed securities absent — more than "
                               f"{max_absent_share:.0%}: the file is treated as truncated, nothing marked")
                return out
            close, inserts = [], []
            for g in missing:
                cur = absent.get(g)
                if cur is None:
                    if listed[g]["value"] == "delisted":
                        continue
                    inserts.append((g, "absent_runs", "1", provider_key, file_as_of, run_id))
                    if g not in last_seen and previous_as_of is not None:
                        inserts.append((g, "last_seen", previous_as_of.isoformat(), provider_key, previous_as_of,
                                        run_id))
                    out.newly_absent += 1
                    continue
                if cur["as_of"] >= file_as_of:
                    continue      # the same file again: counted once
                n = int(cur["value"]) + 1
                close.append(cur["id"])
                inserts.append((g, "absent_runs", str(n), provider_key, file_as_of, run_id))
                out.still_absent += 1
                if n >= DELIST_AFTER and listed[g]["value"] != "delisted":
                    close.append(listed[g]["id"])
                    inserts.append((g, "listing_status", "delisted", provider_key, file_as_of, run_id))
                    out.delisted += 1
            for g in seen:
                if g in absent:
                    close.append(absent[g]["id"])
                    if g in last_seen:
                        close.append(last_seen[g]["id"])
                    out.returned += 1
            if close:
                await conn.execute("UPDATE portfolio.security_reference_attributes SET valid_to = now() "
                                   "WHERE id = ANY($1::uuid[]) AND valid_to IS NULL", close)
            if inserts:
                await conn.executemany(
                    "INSERT INTO portfolio.security_reference_attributes (global_security_id, field, value, "
                    "provider_key, as_of, run_id) VALUES ($1::uuid, $2, $3, $4, $5, $6::uuid)", inserts)
            out.writes = len(close) + len(inserts)
    return out


# ── source files ────────────────────────────────────────────────────────────
async def file_seen(conn, provider_key: str, file_key: str, sha256: str) -> bool:
    return bool(await conn.fetchval(
        "SELECT 1 FROM portfolio.security_master_source_files WHERE provider_key = $1 AND file_key = $2 AND "
        "sha256 = $3 AND status = 'complete' AND " + LIVE, provider_key, file_key, sha256))


async def previous_file(conn, provider_key: str, file_key: str):
    return await conn.fetchrow(
        "SELECT as_of, sha256 FROM portfolio.security_master_source_files WHERE provider_key = $1 AND file_key = $2 "
        "AND status = 'complete' AND " + LIVE + " ORDER BY as_of DESC, created_at DESC LIMIT 1", provider_key, file_key)


async def record_file(pool, *, provider_key: str, file_key: str, as_of, sha256: str, row_count: int, status: str,
                      summary: dict, run_id=None) -> None:
    async with pool.acquire() as conn:
        async with conn.transaction():
            await _platform(conn)
            await conn.execute(
                "INSERT INTO portfolio.security_master_source_files (provider_key, file_key, as_of, sha256, row_count, "
                "status, summary, run_id) VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8::uuid) "
                "ON CONFLICT (provider_key, file_key, sha256) WHERE status = 'complete' AND valid_to IS NULL AND "
                "system_to IS NULL DO NOTHING",
                provider_key, file_key, as_of, sha256, int(row_count), status, json.dumps(summary, default=str), run_id)


# ── reads ───────────────────────────────────────────────────────────────────
async def read_reference(conn, gsid, *, org_id=None, purpose: str = "internal", as_of: date | None = None) -> dict:
    """Every reference field through marketdata1's rank walk for data type 'reference': {field: {value, provider,
    as_of, found, tried}}. ``org_id`` None reads the platform rank."""
    from datetime import datetime, timezone

    from services.security_data import layer as L

    day = as_of or datetime.now(timezone.utc).date()
    subjects = [f"{gsid}:{f}" for f in REFERENCE_FIELDS]
    got = await L.get_bulk(conn, org_id, "reference", subjects, as_of=day, purpose=purpose)
    out = {}
    for f in REFERENCE_FIELDS:
        r = got[f"{gsid}:{f}"]
        out[f] = {"found": r.found, "value": r.value if r.found else None, "provider": r.provider_key,
                  "as_of": r.as_of.isoformat() if r.as_of else None, "tried": r.tried}
    return out


CUSIP_LICENSED = ("cusip", "isin")


async def identifiers_for_display(conn, gsid, *, purpose: str, own_holdings: bool = False) -> list[dict]:
    """Live identifiers of a security for a reader. CUSIP (and the ISIN, which embeds it) are CUSIP Global Services'
    licensed identifiers: the 13(f) provider row says ``own_holdings_only`` — a member sees them only for their own
    holdings; internal use sees them. Tickers and FIGIs are shown. Judged by THE licence rule
    (registry.point_allowed), not a second copy of it."""
    from services.security_data import registry as R
    from services.security_data.types import Point, ReadContext

    rows = await conn.fetch(
        "SELECT i.id_type, i.id_value, i.is_primary FROM portfolio.securities_global_identifiers i "
        "WHERE i.global_security_id = $1::uuid AND i.valid_to IS NULL AND i.system_to IS NULL "
        "ORDER BY i.is_primary DESC, i.id_type", str(gsid))
    provs = await R.providers(conn)
    lic_row = provs.get("sec_13f_list")
    ctx = ReadContext("", purpose)
    out = []
    for r in rows:
        if r["id_type"] in CUSIP_LICENSED:
            if lic_row is None:
                continue
            why = R.point_allowed(lic_row, Point(r["id_value"], date.today(), "sec_13f_list",
                                                 detail={"own_holdings": bool(own_holdings)}), ctx)
            if why is not None:
                continue
        out.append({"id_type": r["id_type"], "id_value": r["id_value"], "is_primary": r["is_primary"]})
    return out
