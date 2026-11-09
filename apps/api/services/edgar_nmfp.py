"""CASH03 — the N-MFP extractor on the EDGAR pipeline: money-market fund 7-day yields, WAM and WAL.

Form N-MFP2 (until June 2024) / N-MFP3 (after the 2023 money-market-fund amendments): every money-market fund series
files one monthly XML report (``primary_doc.xml``) within five business days of month end. An SEC public record —
the provider row 'sec_nmfp' (docs/cash03_part1.sql) carries the licence facts; shipped NOT CONNECTED.

THE EDGAR PIPELINE, REUSED (docs/cash/CASH03_NOTES.md §3):
    discovery   services.edgar_index.parse_index_lines(lines, forms=NMFP_FORMS) over an EDGAR master index
                (services.edgar_fetch.INDEX_URL) — the ``forms`` override bulkfetch added
    fetch       every sec.gov request goes through the injected SourceTransport; the real one
                (reference_common.RealSourceTransport) sends it through services.edgar_fetch._get — the declared
                User-Agent and the shared 10/s limiter (SEC fair access)
    filing URL  services.edgar_fetch.FilingMeta(...).archive_base + "/primary_doc.xml"
  N-MFP filings are NOT inserted into portfolio.edgar_index_filings: that table's status machine (select → R2 store
  → classify) is the structured-note pipeline's. The output here is a yield, not a stored document.

WHAT A FILING YIELDS (local element names; namespaces ignored — CASH03_NOTES §3 lists them):
    reportDate · seriesId (S + 9 digits) · moneyMarketFundCategory · averagePortfolioMaturity (WAM, days) ·
    averageLifeMaturity (WAL, days) · per class: classesId (C + 9 digits), the class name, sevenDayNetYield
  The 7-day net yield is reported as a DECIMAL FRACTION (0.0512 = 5.12 %): it is multiplied by 100. It may be a bare
  value (dated reportDate) or a list of dated values (the latest date wins). Written to portfolio.index_levels as
  nmfp:<class id>:7d (percent), nmfp:<series id>:wam and :wal (days).

REFUSED, with the reason — never guessed (``FilingRejected``): not XML, a DOCTYPE / ENTITY (never expanded), no
reportDate, no / malformed seriesId, an unknown category, no class, a class with no / malformed classesId, no 7-day
yield, a value above 1 ("not a fraction") or outside [−5 %, 25 %] once scaled. A class without a usable yield
rejects the WHOLE filing (a half-read fund report is not a yield source).

No live filing was fetched by the sprint (no network): the element names follow the forms' published XML technical
specifications, and the operator's first ``load_cash_market_data.py --real`` prints each real parse before use.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from services.security_data.providers import store
from services.security_data.providers.base import FetchOutcome
from services.security_data.providers.reference_common import (BudgetExhausted, CallBudget, ReferenceProvider,
                                                              SourceRecord, read_text)

KEY = "sec_nmfp"
NMFP_FORMS = ("N-MFP2", "N-MFP3", "N-MFP2/A", "N-MFP3/A")
PRIMARY_DOC = "primary_doc.xml"
#: THE staleness rule's limit for this source: a month's report lands ~5 business days after month end, so the newest
#: stored yield is at most ~40 days old while the monthly refresh runs
STALE_DAYS = 45
#: the monthly cadence: from this day of the month, the nightly's cash_sources step fetches once per month
MONTHLY_FROM_DAY = 8
LICENCE = {"internal_use": True, "member_display": "allowed", "redistribution": True, "retention_after_cancel": "keep",
           "attribution": "Source: U.S. Securities and Exchange Commission, EDGAR (Form N-MFP)"}
BASIS = "7-day net yield (SEC Form N-MFP)"
_SERIES = re.compile(r"^S\d{9}$")
_CLASS = re.compile(r"^C\d{9}$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")
CATEGORIES = {"treasury": "treasury", "exempt government": "government", "government": "government",
              "government/agency": "government", "government agency": "government", "prime": "prime",
              "single state": "single_state", "other tax exempt": "other_tax_exempt", "tax exempt": "other_tax_exempt",
              "tax-exempt": "other_tax_exempt"}
FRACTION_MIN, FRACTION_MAX = Decimal("-0.05"), Decimal("0.25")


class FilingRejected(ValueError):
    """The whole filing is refused — the message is the reason."""


@dataclass
class NmfpClass:
    class_id: str
    name: str | None
    yield_pct: Decimal          # percent
    yield_date: date


@dataclass
class NmfpFiling:
    series_id: str
    series_name: str | None
    report_date: date
    category: str               # treasury | government | prime | single_state | other_tax_exempt
    wam_days: Decimal | None
    wal_days: Decimal | None
    classes: list[NmfpClass] = field(default_factory=list)
    form: str | None = None


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _children(el, name: str) -> list:
    return [c for c in el if _local(c.tag) == name]


def _first_text(el, *names: str) -> str | None:
    want = set(names)
    for c in el.iter():
        if _local(c.tag) in want and (c.text or "").strip():
            return c.text.strip()
    return None


def _date(s: str | None, what: str) -> date:
    if not s or not _DATE.match(s):
        raise FilingRejected(f"{what} {s!r} is not a date")
    return date.fromisoformat(s[:10])


def _days(s: str | None) -> Decimal | None:
    if s is None:
        return None
    try:
        d = Decimal(s)
    except InvalidOperation:
        return None
    return d if d.is_finite() and d >= 0 else None


def _fraction(s: str, what: str) -> Decimal:
    try:
        v = Decimal(s.strip())
    except (InvalidOperation, AttributeError):
        raise FilingRejected(f"{what} {s!r} is not a number") from None
    if not v.is_finite():
        raise FilingRejected(f"{what} {s!r} is not a number")
    if abs(v) > 1:
        raise FilingRejected(f"{what} {s} is not a fraction (N-MFP reports yields as decimals, 0.0512 = 5.12 %)")
    if not FRACTION_MIN <= v <= FRACTION_MAX:
        raise FilingRejected(f"{what} {v} is outside [{FRACTION_MIN}, {FRACTION_MAX}]")
    return v


def _class_yield(cls_el, report_date: date, class_id: str) -> tuple[Decimal, date]:
    """The class's latest 7-day net yield (percent) and its date."""
    points: list[tuple[date, Decimal]] = []
    for y in cls_el.iter():
        if _local(y.tag) != "sevenDayNetYield":
            continue
        kids = list(y)
        if not kids:
            if (y.text or "").strip():
                points.append((report_date, _fraction(y.text, f"{class_id}: sevenDayNetYield")))
            continue
        dt = next((k.text for k in y.iter() if "date" in _local(k.tag).lower() and (k.text or "").strip()), None)
        val = next((k.text for k in y.iter() if "value" in _local(k.tag).lower() and (k.text or "").strip()), None)
        if val is None:
            continue
        points.append((_date(dt.strip(), f"{class_id}: yield date") if dt else report_date,
                       _fraction(val, f"{class_id}: sevenDayNetYield")))
    if not points:
        raise FilingRejected(f"{class_id}: no 7-day net yield")
    d, v = max(points, key=lambda p: p[0])
    return v * 100, d


def parse_nmfp(raw) -> NmfpFiling:
    """One N-MFP primary_doc.xml → NmfpFiling. Raises FilingRejected with the reason."""
    text = raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else str(raw or "")
    if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise FilingRejected("the document declares a DOCTYPE / ENTITY — refused (never expanded)")
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise FilingRejected(f"not XML ({exc})") from None
    form = _first_text(root, "submissionType")
    report = _date(_first_text(root, "reportDate"), "reportDate")
    series = (_first_text(root, "seriesId") or "").strip()
    if not _SERIES.match(series):
        raise FilingRejected(f"seriesId {series or '(none)'!r} is not S + 9 digits")
    raw_cat = (_first_text(root, "moneyMarketFundCategory") or "").strip()
    cat = CATEGORIES.get(raw_cat.lower())
    if cat is None:
        raise FilingRejected(f"moneyMarketFundCategory {raw_cat or '(none)'!r} is not a known category")
    out = NmfpFiling(series_id=series, series_name=_first_text(root, "nameOfSeries", "seriesName"),
                     report_date=report, category=cat, wam_days=_days(_first_text(root, "averagePortfolioMaturity")),
                     wal_days=_days(_first_text(root, "averageLifeMaturity")), form=form)
    class_els = [el for el in root.iter() if _local(el.tag) == "classLevelInfo"]
    if not class_els:
        raise FilingRejected("no classLevelInfo — a filing names at least one share class")
    seen = set()
    for c in class_els:
        cid = (_first_text(c, "classesId", "classId") or "").strip()
        if not _CLASS.match(cid):
            raise FilingRejected(f"classesId {cid or '(none)'!r} is not C + 9 digits")
        if cid in seen:
            continue
        seen.add(cid)
        y, d = _class_yield(c, report, cid)
        if not Decimal("-5") <= y <= Decimal("25"):
            raise FilingRejected(f"{cid}: 7-day yield {y} % is outside [−5, 25] %")
        out.classes.append(NmfpClass(cid, _first_text(c, "classFullName", "nameOfClass", "className"), y, d))
    return out


def yield_key(class_id: str) -> str:
    return f"nmfp:{class_id}:7d"


def wam_key(series_id: str) -> str:
    return f"nmfp:{series_id}:wam"


def wal_key(series_id: str) -> str:
    return f"nmfp:{series_id}:wal"


def source_records(f: NmfpFiling, as_of: date) -> list[SourceRecord]:
    """The security-master records (through Matcher('sec_nmfp')): one per share class, matched on its SEC class id
    (created by class id when absent — a money-market class need not have a ticker)."""
    out = []
    for c in f.classes:
        nm = c.name or f.series_name or "Money market fund"
        out.append(SourceRecord(provider_key=KEY, name=f"{nm} (SEC share class {c.class_id})", security_type="fund",
                                attrs={"fund_structure": "money_market", "mmf_category": f.category},
                                extra_ids={"sec_series_id": f.series_id, "sec_class_id": c.class_id}, as_of=as_of))
    return out


def points(f: NmfpFiling) -> dict[str, list[tuple[date, Decimal]]]:
    out = {yield_key(c.class_id): [(c.yield_date, c.yield_pct)] for c in f.classes}
    if f.wam_days is not None:
        out[wam_key(f.series_id)] = [(f.report_date, f.wam_days)]
    if f.wal_days is not None:
        out[wal_key(f.series_id)] = [(f.report_date, f.wal_days)]
    return out


def index_url(day: date) -> str:
    from services import edgar_fetch

    return edgar_fetch.INDEX_URL.format(year=day.year, quarter=(day.month - 1) // 3 + 1)


def filings_from_index(text: str, ciks: set[str] | None = None) -> list:
    """N-MFP filings in an EDGAR master index, newest first per (CIK, form family) — as edgar_fetch.FilingMeta."""
    from services import edgar_fetch, edgar_index

    parsed = edgar_index.parse_index_lines(text.splitlines(), forms=NMFP_FORMS)
    names = {acc: nm for (acc, _cik), nm in parsed.filers.items()}
    ciks_of: dict[str, set[str]] = {}
    for acc, cik in parsed.filers:
        ciks_of.setdefault(acc, set()).add(cik)
    out = []
    for acc, (form, filed, path) in parsed.filings.items():
        for cik in sorted(ciks_of.get(acc, ())):
            if ciks is not None and cik not in ciks:
                continue
            out.append(edgar_fetch.FilingMeta(cik=cik, filer_name=names.get(acc, ""), form_type=form,
                                              accession_number=acc, filing_date=filed, submission_path=path,
                                              primary_document=PRIMARY_DOC))
    out.sort(key=lambda m: (m.filing_date, m.accession_number), reverse=True)
    return out


def due(today: date, last_ok: date | None) -> bool:
    """The monthly cadence: once a calendar month, from the MONTHLY_FROM_DAY-th."""
    if today.day < MONTHLY_FROM_DAY:
        return False
    return last_ok is None or (last_ok.year, last_ok.month) < (today.year, today.month)


@dataclass
class RefreshResult:
    filings: int = 0
    parsed: list[NmfpFiling] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)
    master: dict = field(default_factory=dict)
    yields_written: int = 0
    calls: int = 0


async def refresh(pool, transport, *, today: date, ciks: set[str] | None, series_ids: set[str] | None = None,
                  run_id=None, budget: CallBudget | None = None, dry_run: bool = False,
                  max_filings: int = 200) -> RefreshResult:
    """The latest N-MFP filing per series for ``ciks`` (registrant CIKs, unpadded) from this quarter's master index
    (and the previous quarter's, early in a quarter) → parse → the security master + index_levels. A rejected filing
    is recorded and skipped; nothing of it is written. ``dry_run`` parses and reports only."""
    from services import security_master_load as M
    from services.security_data import writes as W

    res = RefreshResult()
    if not dry_run:
        budget = budget or await CallBudget(pool, KEY, run_id=run_id, data_type="index_level", today=today).load()
    days = [today] + ([today - timedelta(days=40)] if (today.month - 1) % 3 == 0 and today.day <= 15 else [])
    metas = []
    try:
        for d in days:
            if budget is not None and not dry_run:
                await budget.take()
            metas += filings_from_index(await read_text(transport, index_url(d)), ciks)
        by_series: dict[str, NmfpFiling] = {}
        for meta in metas[:max_filings]:
            if budget is not None and not dry_run:
                await budget.take()
            res.filings += 1
            try:
                f = parse_nmfp(await read_text(transport, f"{meta.archive_base}/{PRIMARY_DOC}"))
            except FilingRejected as exc:
                res.rejected.append(f"{meta.accession_number}: {exc}")
                continue
            if series_ids is not None and f.series_id not in series_ids:
                continue
            cur = by_series.get(f.series_id)
            if cur is None or f.report_date > cur.report_date:
                by_series[f.series_id] = f
    except BudgetExhausted as exc:
        res.rejected.append(str(exc))
    res.parsed = sorted(by_series.values(), key=lambda f: f.series_id) if metas else []
    res.calls = budget.used if budget is not None else 0
    if dry_run:
        return res
    records = [r for f in res.parsed for r in source_records(f, today)]
    if records:
        res.master = (await M.Matcher(KEY, as_of=today, run_id=run_id).process(pool, records)).summary()
    for f in res.parsed:
        for key, pts in points(f).items():
            res.yields_written += await W.upsert_index_levels(pool, key, [(d, v) for d, v in pts if d <= today],
                                                              provider_key=KEY, licence=dict(LICENCE))
    await budget.flush(ok=not res.rejected, error="; ".join(res.rejected)[:2000] or None)
    return res


class NmfpProvider(ReferenceProvider):
    """The code-registry module. Keyless, so ``connected``. read() answers the rank walk from the canonical stores:
    'index_level' (nmfp:<class>:7d, :wam, :wal) and 'reference' (the class's category). ``fetches`` is empty: the
    nightly's cash_sources step runs ``refresh`` on the monthly cadence."""

    capabilities = frozenset({"index_level", "reference"})
    fetches: frozenset[str] = frozenset()
    connected = True
    cadence = "monthly"

    def __init__(self, *, transport=None):
        super().__init__(KEY)
        self.transport = transport

    async def read(self, conn, ctx, data_type, subjects, *, end: date, start: date | None = None) -> dict:
        if data_type == "index_level":
            return await store.index_levels(conn, ctx.org_id, self.key, subjects, end=end, start=start)
        return await super().read(conn, ctx, data_type, subjects, end=end, start=start)

    async def fetch(self, pool, data_type, subjects, *, today: date, budget_left):
        return FetchOutcome(skipped=[f"{KEY}: refreshed by the nightly's cash_sources step (monthly)"])

    async def refresh(self, pool, *, today: date, ciks: set[str] | None, run_id=None, transport=None):
        from services.security_data.providers.reference_common import RealSourceTransport

        t = transport or self.transport
        own = t is None
        t = t or RealSourceTransport()
        try:
            return await refresh(pool, t, today=today, ciks=ciks, run_id=run_id)
        finally:
            if own:
                await t.aclose()
