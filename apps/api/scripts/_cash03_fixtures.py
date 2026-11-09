"""CASH03 — the recorded fixture payloads (apps/api/tests/fixtures/cash03) behind a FixtureTransport, keyed by the
EXACT URLs the providers request. Used by verify_cash03.py and load_cash_market_data.py --dry-run (no network)."""

from __future__ import annotations

import pathlib
from datetime import date, timedelta

FIXTURES = pathlib.Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "cash03"
#: the fixture EDGAR filer CIK → its N-MFP primary document
NMFP_BY_CIK = {"9900301": "nmfp_treasury.xml", "9900302": "nmfp_government.xml", "9900303": "nmfp_muni.xml",
               "9900304": "nmfp_bad_no_yield.xml"}
FIXTURE_CIKS = set(NMFP_BY_CIK)


def text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def treasury_files(today: date) -> dict[str, str]:
    from services.market_data import treasury_fiscal as TF

    since = today - timedelta(days=TF.LOOKBACK_DAYS)
    return {TF.auctions_url(since, 1): text("tfd_auctions_p1.json"), TF.auctions_url(since, 2): text("tfd_auctions_p2.json")}


def nmfp_files(today: date) -> dict[str, str]:
    from services import edgar_nmfp as NM

    idx = text("edgar_master_idx.txt")
    files = {NM.index_url(today): idx, NM.index_url(today - timedelta(days=40)): idx}
    for meta in NM.filings_from_index(idx, None):
        if meta.cik in NMFP_BY_CIK:
            files[f"{meta.archive_base}/{NM.PRIMARY_DOC}"] = text(NMFP_BY_CIK[meta.cik])
    return files


def transport(today: date, *, extra: dict | None = None):
    from services.security_data.providers.reference_common import FixtureTransport

    return FixtureTransport({**treasury_files(today), **nmfp_files(today), **(extra or {})})


class NoBudget:
    """A CallBudget stand-in for fixture runs: counts, never writes provider_call_log, never refuses. Matches
    CallBudget's interface (take / flush / used / remaining)."""

    def __init__(self) -> None:
        self.used = 0

    async def take(self, n: int = 1) -> None:
        self.used += n

    async def flush(self, *, ok: bool = True, error: str | None = None) -> None:
        return None

    def remaining(self):
        return None
