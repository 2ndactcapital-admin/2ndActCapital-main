"""sec_13f_list — the SEC Official List of Section 13(f) Securities (quarterly CUSIP list).

WHERE. The list page (INDEX_URL) links each quarter's text file as /files/investment/13flist{YYYY}q{N}.txt —
some quarters carry a '-txt' suffix (13flist2026q2-txt.txt), so the link is READ from the page, never built.
The newest quarter on the page is the current one; a quarter already recorded complete in
portfolio.security_master_source_files is not loaded again.

FORMAT (sampled 2026-11-04, 13flist2026q3.txt): fixed width, 80 characters, no header:

    [0:9]   CUSIP                         B38564108
    [9]     '*' = options on this security are on the list
    [10:40] issuer                        CMB.TECH NV
    [40:67] description                   SHS  /  CALL  /  PUT  /  *W EXP 08/28/203  /  UNIT 99/99/9999
    [67:70] status                        '*A*' added this quarter, '*D*' deleted this quarter, blank unchanged
    [79]    'E' (constant)

An OPTION row is one whose description is CALL or PUT: it is kept in the table (is_option) and never mapped or
loaded as a security. A line whose first nine characters are not a CUSIP (a page header, a blank) is skipped
and counted. Two requests per check at most: the page, and the file when the quarter is new."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Iterator

from services.security_data.providers.reference_common import (Digest, ParseStats, ReferenceProvider,
                                                              SourceTransport, read_text)

KEY = "sec_13f_list"
INDEX_URL = "https://www.sec.gov/rules-regulations/staff-guidance/official-list-section-13f-securities"
BASE = "https://www.sec.gov"
_LINK = re.compile(r'href="(/files/investment/13flist(\d{4})q([1-4])(?:-txt)?\.txt)"', re.I)
_CUSIP = re.compile(r"^[0-9A-Z*@#]{9}$")
STATUS = {"*A*": "added", "*D*": "deleted"}
OPTION_DESCRIPTIONS = ("CALL", "PUT")


class Sec13fProvider(ReferenceProvider):
    def __init__(self) -> None:
        super().__init__(KEY)


@dataclass
class Row13F:
    cusip: str
    has_listed_options: bool
    issuer: str
    description: str
    status: str
    is_option: bool


def find_quarters(html: str) -> list[tuple[str, str]]:
    """[(quarter 'YYYYqN', absolute url)] newest first, de-duplicated."""
    seen: dict[str, str] = {}
    for path, yy, q in _LINK.findall(html or ""):
        seen.setdefault(f"{yy}q{q}", BASE + path)
    return sorted(seen.items(), key=lambda kv: kv[0], reverse=True)


def parse_line(line: str) -> Row13F | None:
    ln = (line or "").rstrip("\r\n")
    if len(ln) < 11:
        return None
    cusip = ln[0:9].strip().upper()
    if not _CUSIP.match(cusip):
        return None
    ln = ln.ljust(80)
    desc = ln[40:67].strip()
    return Row13F(cusip=cusip, has_listed_options=ln[9] == "*", issuer=ln[10:40].strip(), description=desc,
                  status=STATUS.get(ln[67:70], "unchanged"), is_option=desc.upper() in OPTION_DESCRIPTIONS)


def parse(lines: Iterable[str], stats: ParseStats) -> Iterator[Row13F]:
    for ln in lines:
        stats.lines += 1
        if not ln.strip():
            continue
        row = parse_line(ln)
        if row is None:
            stats.exclude("not a CUSIP line")
            continue
        stats.rows += 1
        if row.is_option:
            stats.exclude("option (CALL / PUT) — kept in the list, never loaded as a security")
        else:
            stats.kept += 1
        yield row


async def latest_quarter(transport: SourceTransport, *, budget=None) -> tuple[str, str] | None:
    if budget is not None:
        await budget.take()
    html = await read_text(transport, INDEX_URL)
    q = find_quarters(html)
    return q[0] if q else None


async def fetch_quarter(transport: SourceTransport, url: str, *, budget=None) -> tuple[list[Row13F], ParseStats]:
    """~23k rows × 80 bytes (~2 MB) through edgar_fetch; parsed line by line."""
    if budget is not None:
        await budget.take()
    digest = Digest()
    stats = ParseStats()
    rows: list[Row13F] = []
    async for ln in transport.lines(url):
        digest.update(ln)
        rows.extend(parse([ln], stats))
    stats.sha256 = digest.hexdigest()
    return rows, stats
