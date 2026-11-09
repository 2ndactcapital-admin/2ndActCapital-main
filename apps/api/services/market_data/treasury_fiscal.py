"""CASH03 — U.S. Treasury Fiscal Data: auctioned bills and notes (CUSIP, maturity, auction yield).

    GET https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/accounting/od/auctions_query
        ?fields=…&filter=auction_date:gte:<date>&sort=-auction_date&page[size]=…&page[number]=…

A work of the U.S. Government — PUBLIC DOMAIN (17 U.S.C. 105); no key, no registration. The licence facts live on
the provider's registry row (portfolio.data_providers 'treasury_fiscal', docs/cash03_part1.sql) and are mirrored in
``LICENCE`` here only so a test can assert they agree. Shipped NOT CONNECTED: ``CallBudget.load`` refuses to fetch
for a provider whose status is not 'active', and the read path skips it as "not connected" — a Super Admin switches
it on in the provider admin.

SHAPE (follows market_data/fred.py and the marketdata1 provider contract): the network boundary is an INJECTED
``SourceTransport`` (``RealSourceTransport`` for a real run; ``FixtureTransport`` with recorded payloads for tests —
the verify proves no network by that, plus httpx refusing every request). Parsing is pure and Decimal-only.

PARSE RULES (``parse_auctions``) — a whole payload is REFUSED with a reason, never half-read:
    not JSON / not an object / no ``data`` list                       → PayloadRejected
    a partial page (``meta.count`` ≠ the rows in ``data``)             → PayloadRejected
  per row (the row is skipped and its reason recorded; the rest of the page stands):
    no / malformed CUSIP (9 characters, valid check digit)              · an unknown security_type
    no maturity_date, or one that is not after the issue date           · a yield that is not a number
  The source's missing-value marker is the text "null". The yield is the auction's ``high_yield`` (notes, bonds) —
  a bill's high_yield is "null", so a bill uses ``high_investment_rate`` (the bond-equivalent investment rate) and
  says so in its basis. The discount rate is never treated as a yield. TIPS and FRNs are recorded in the security
  master but are not ladder candidates (a real yield / a discount margin is not comparable).

WRITES (``refresh``): issues → the security master through ``security_master_load.Matcher('treasury_fiscal')``
(fixed_income; CUSIP + derived ISIN; treasury_type, security_term, issue_date, maturity_date, country); each auction's
yield → ``portfolio.index_levels`` as ``tfd:<cusip>:yield`` dated the auction date (security_data.writes, valid-axis
restatement). One provider_call_log row per run (``CallBudget``).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlencode

from services import security_master_symbols as SYM
from services.security_data.providers import store
from services.security_data.providers.base import FetchOutcome
from services.security_data.providers.reference_common import (BudgetExhausted, CallBudget, ReferenceProvider,
                                                              SourceRecord, read_text)

KEY = "treasury_fiscal"
BASE = "https://api.fiscaldata.treasury.gov/services/api/fiscal_service"
AUCTIONS_PATH = "/v1/accounting/od/auctions_query"
UPCOMING_PATH = "/v1/accounting/od/upcoming_auctions"     # not used by cash03 (cash04's reinvestment)
FIELDS = ("cusip", "security_type", "security_term", "auction_date", "issue_date", "maturity_date", "high_yield",
          "high_investment_rate", "high_discount_rate", "int_rate", "tips", "floating_rate")
PAGE_SIZE = 1000
MAX_PAGES = 10
#: a new run reads every auction of the last ~3 years: a 3-year note auctioned 35 months ago still matures inside a
#: 24-month horizon; bills (≤ 52 weeks) and 2-year notes are well inside it
LOOKBACK_DAYS = 1100
#: THE staleness rule's limit for this source (security_data.settings.is_stale): auctions run every week, so a newest
#: stored auction older than this means the daily refresh has stopped
STALE_DAYS = 10
NULL = "null"
SECURITY_TYPES = ("Bill", "Note", "Bond", "CMB", "TIPS", "FRN")
LADDER_TYPES = ("Bill", "Note")          # nominal, fixed: the ladder's instruments
BILL_LIKE = ("Bill", "CMB")
LICENCE = {"internal_use": True, "member_display": "allowed", "redistribution": True, "retention_after_cancel": "keep",
           "attribution": "Source: U.S. Department of the Treasury, Fiscal Data (fiscaldata.treasury.gov)"}
BASIS_YIELD = "auction high yield"
BASIS_INVESTMENT = "auction high investment rate (bond-equivalent)"
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
YIELD_MIN, YIELD_MAX = Decimal("-5"), Decimal("25")


class PayloadRejected(ValueError):
    """The whole payload is refused — the message is the reason."""


@dataclass
class Auction:
    cusip: str
    security_type: str          # Bill | Note | Bond | CMB | TIPS | FRN
    security_term: str
    auction_date: date
    issue_date: date | None
    maturity_date: date
    yield_pct: Decimal | None   # percent (4.25 = 4.25 %)
    basis: str | None

    @property
    def ladder_candidate(self) -> bool:
        return self.security_type in LADDER_TYPES

    @property
    def index_key(self) -> str:
        return index_key(self.cusip)

    def name(self) -> str:
        kind = {"Bill": "Bill", "CMB": "Cash Management Bill", "Note": "Note", "Bond": "Bond", "TIPS": "TIPS",
                "FRN": "Floating Rate Note"}[self.security_type]
        return f"U.S. Treasury {kind} {self.security_term} due {self.maturity_date.isoformat()}".strip()


@dataclass
class ParsedAuctions:
    auctions: list[Auction] = field(default_factory=list)
    rejected: list[tuple[int, str]] = field(default_factory=list)   # (row index, reason)
    total_pages: int | None = None
    page_count: int = 0


def index_key(cusip: str) -> str:
    return f"tfd:{cusip}:yield"


def _text(v) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return None if s == "" or s.lower() == NULL else s


def _date(v, what: str) -> date | None:
    s = _text(v)
    if s is None:
        return None
    if not _DATE.match(s[:10]):
        raise ValueError(f"{what} {s!r} is not a date")
    return date.fromisoformat(s[:10])


def _pct(v, what: str) -> Decimal | None:
    s = _text(v)
    if s is None:
        return None
    try:
        d = Decimal(s)
    except InvalidOperation:
        raise ValueError(f"{what} {s!r} is not a number") from None
    if not d.is_finite() or not YIELD_MIN <= d <= YIELD_MAX:
        raise ValueError(f"{what} {s} is outside [{YIELD_MIN}, {YIELD_MAX}] %")
    return d


def _security_type(row: dict) -> str | None:
    t = _text(row.get("security_type"))
    if t is None:
        return None
    if (_text(row.get("tips")) or "").lower() == "yes":
        return "TIPS"
    if (_text(row.get("floating_rate")) or "").lower() == "yes":
        return "FRN"
    t = t.strip()
    for k in SECURITY_TYPES:
        if t.lower() == k.lower():
            return k
    return None


def parse_row(row: dict) -> Auction:
    """One auction row → an Auction. Raises ValueError with the reason (the caller records it)."""
    if not isinstance(row, dict):
        raise ValueError("a row is not an object")
    cusip = (_text(row.get("cusip")) or "").upper()
    if not SYM.is_valid_cusip(cusip):
        raise ValueError(f"CUSIP {cusip or '(none)'!r} is not a valid 9-character CUSIP")
    st = _security_type(row)
    if st is None:
        raise ValueError(f"{cusip}: security_type {row.get('security_type')!r} is not one of {', '.join(SECURITY_TYPES)}")
    auction = _date(row.get("auction_date"), "auction_date")
    if auction is None:
        raise ValueError(f"{cusip}: no auction_date")
    issue = _date(row.get("issue_date"), "issue_date")
    maturity = _date(row.get("maturity_date"), "maturity_date")
    if maturity is None:
        raise ValueError(f"{cusip}: no maturity_date")
    if issue is not None and maturity <= issue:
        raise ValueError(f"{cusip}: maturity {maturity} is not after issue {issue}")
    if st in BILL_LIKE:
        y, basis = _pct(row.get("high_investment_rate"), f"{cusip}: high_investment_rate"), BASIS_INVESTMENT
    else:
        y, basis = _pct(row.get("high_yield"), f"{cusip}: high_yield"), BASIS_YIELD
    return Auction(cusip, st, (_text(row.get("security_term")) or "").strip(), auction, issue, maturity, y,
                   basis if y is not None else None)


def parse_auctions(payload: Any) -> ParsedAuctions:
    """A page of auctions_query → ParsedAuctions. Raises PayloadRejected for a payload that cannot be read whole."""
    if isinstance(payload, (bytes, bytearray)):
        payload = payload.decode("utf-8", "replace")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except ValueError as exc:
            raise PayloadRejected(f"not JSON ({exc.msg})") from None
    if not isinstance(payload, dict):
        raise PayloadRejected("not a JSON object")
    data = payload.get("data")
    if not isinstance(data, list):
        raise PayloadRejected("no 'data' list — not an auctions_query response")
    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    if "count" in meta:
        try:
            n = int(meta["count"])
        except (TypeError, ValueError):
            raise PayloadRejected(f"meta.count {meta['count']!r} is not a number") from None
        if n != len(data):
            raise PayloadRejected(f"partial page: meta.count says {n} rows, 'data' holds {len(data)}")
    out = ParsedAuctions(page_count=1)
    try:
        out.total_pages = int(meta["total-pages"]) if "total-pages" in meta else None
    except (TypeError, ValueError):
        raise PayloadRejected(f"meta.total-pages {meta.get('total-pages')!r} is not a number") from None
    seen: set[tuple[str, date]] = set()
    for i, row in enumerate(data):
        try:
            a = parse_row(row)
        except ValueError as exc:
            out.rejected.append((i, str(exc)))
            continue
        if (a.cusip, a.auction_date) in seen:          # a reopening is a new auction date; the same row twice is not
            out.rejected.append((i, f"{a.cusip}: duplicate row for auction {a.auction_date}"))
            continue
        seen.add((a.cusip, a.auction_date))
        out.auctions.append(a)
    return out


def auctions_url(since: date, page: int, page_size: int = PAGE_SIZE) -> str:
    q = {"fields": ",".join(FIELDS), "filter": f"auction_date:gte:{since.isoformat()}", "sort": "-auction_date",
         "page[size]": str(page_size), "page[number]": str(page)}
    return f"{BASE}{AUCTIONS_PATH}?{urlencode(q, safe=',:[]')}"


def issues(auctions: list[Auction]) -> list[Auction]:
    """One row per CUSIP: its LATEST auction (a reopening's newer yield and the same maturity)."""
    best: dict[str, Auction] = {}
    for a in auctions:
        cur = best.get(a.cusip)
        if cur is None or a.auction_date > cur.auction_date:
            best[a.cusip] = a
    return sorted(best.values(), key=lambda a: (a.maturity_date, a.cusip))


def source_records(auctions: list[Auction], as_of: date) -> list[SourceRecord]:
    """The security-master records (through Matcher): fixed income, the CUSIP, the issue facts."""
    out = []
    for a in issues(auctions):
        attrs = {"treasury_type": a.security_type, "security_term": a.security_term or None,
                 "maturity_date": a.maturity_date.isoformat(), "country": "US"}
        if a.issue_date:
            attrs["issue_date"] = a.issue_date.isoformat()
        out.append(SourceRecord(provider_key=KEY, name=a.name(), security_type="fixed_income", cusip=a.cusip,
                                attrs=attrs, as_of=as_of))
    return out


def yield_points(auctions: list[Auction]) -> dict[str, list[tuple[date, Decimal]]]:
    """{index key: [(auction date, yield %)]} — every auction with a yield (a reopening adds a point)."""
    out: dict[str, list[tuple[date, Decimal]]] = {}
    for a in auctions:
        if a.yield_pct is not None:
            out.setdefault(a.index_key, []).append((a.auction_date, a.yield_pct))
    return {k: sorted(v) for k, v in out.items()}


@dataclass
class RefreshResult:
    pages: int = 0
    auctions: int = 0
    rejected: list[str] = field(default_factory=list)
    issues: int = 0
    master: dict = field(default_factory=dict)
    yields_written: int = 0
    calls: int = 0


async def fetch_all(transport, since: date, *, budget: CallBudget | None = None, max_pages: int = MAX_PAGES,
                    page_size: int = PAGE_SIZE) -> tuple[list[Auction], list[str], int]:
    """Every page since ``since`` through the injected transport → (auctions, rejected reasons, pages read). A
    rejected PAGE stops the read (raises PayloadRejected): nothing from a half-read source is written."""
    auctions, rejected, page = [], [], 1
    while page <= max_pages:
        if budget is not None:
            await budget.take()
        parsed = parse_auctions(await read_text(transport, auctions_url(since, page, page_size)))
        auctions += parsed.auctions
        rejected += [f"page {page} row {i}: {why}" for i, why in parsed.rejected]
        if parsed.total_pages is None or page >= parsed.total_pages:
            return auctions, rejected, page
        page += 1
    return auctions, rejected + [f"stopped after {max_pages} pages (more remain)"], max_pages


async def refresh(pool, transport, *, today: date, lookback_days: int = LOOKBACK_DAYS, run_id=None,
                  budget: CallBudget | None = None, dry_run: bool = False) -> RefreshResult:
    """Fetch → parse → the security master (Matcher) + the yields (index_levels). ``dry_run`` parses and reports
    only (no write, no budget). Refuses when the provider is not active (CallBudget.load)."""
    from services import security_master_load as M
    from services.security_data import writes as W

    res = RefreshResult()
    if not dry_run:
        budget = budget or await CallBudget(pool, KEY, run_id=run_id, data_type="index_level", today=today).load()
    try:
        auctions, rejected, pages = await fetch_all(transport, today - timedelta(days=lookback_days),
                                                    budget=None if dry_run else budget)
    except (PayloadRejected, BudgetExhausted) as exc:
        if budget is not None and not dry_run:
            await budget.flush(ok=False, error=str(exc))
        raise
    res.pages, res.auctions, res.rejected = pages, len(auctions), rejected
    res.issues = len(issues(auctions))
    res.calls = budget.used if budget is not None else pages
    if dry_run:
        return res
    batch = await M.Matcher(KEY, as_of=today, run_id=run_id).process(pool, source_records(auctions, today))
    res.master = batch.summary()
    for key, pts in yield_points(auctions).items():
        res.yields_written += await W.upsert_index_levels(pool, key, [(d, v) for d, v in pts if d <= today],
                                                          provider_key=KEY, licence=dict(LICENCE))
    await budget.flush(ok=not rejected, error="; ".join(rejected)[:2000] or None)
    return res


class TreasuryFiscalProvider(ReferenceProvider):
    """The code-registry module (services.security_data.providers.build_providers). Keyless, so ``connected``: a
    Super Admin may set it active. read() answers the rank walk from the canonical stores (DB only): 'index_level'
    (tfd:<cusip>:yield) and 'reference' (the issue facts). ``fetches`` is empty: marketdata1's refresh step never
    calls it — the nightly's cash_sources step does (its own cadence, CASH03)."""

    capabilities = frozenset({"index_level", "reference"})
    fetches: frozenset[str] = frozenset()
    connected = True
    cadence = "daily"

    def __init__(self, *, transport=None):
        super().__init__(KEY)
        self.transport = transport

    async def read(self, conn, ctx, data_type, subjects, *, end: date, start: date | None = None) -> dict:
        if data_type == "index_level":
            return await store.index_levels(conn, ctx.org_id, self.key, subjects, end=end, start=start)
        return await super().read(conn, ctx, data_type, subjects, end=end, start=start)

    async def fetch(self, pool, data_type, subjects, *, today: date, budget_left):
        return FetchOutcome(skipped=[f"{KEY}: refreshed by the nightly's cash_sources step"])

    async def refresh(self, pool, *, today: date, run_id=None, transport=None) -> RefreshResult:
        from services.security_data.providers.reference_common import RealSourceTransport

        t = transport or self.transport
        own = t is None
        t = t or RealSourceTransport()
        try:
            return await refresh(pool, t, today=today, run_id=run_id)
        finally:
            if own:
                await t.aclose()
