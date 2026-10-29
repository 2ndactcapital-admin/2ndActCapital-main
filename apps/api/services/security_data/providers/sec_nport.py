"""regulator_filing — SEC EDGAR Form N-PORT-P (rep1's sec_nport parser).
Public record; months old by design. See holdings_common."""

from services.security_data.providers.holdings_common import HoldingsProvider


class NportProvider(HoldingsProvider):
    def __init__(self, *, http=None):
        super().__init__("sec_nport", http=http)
