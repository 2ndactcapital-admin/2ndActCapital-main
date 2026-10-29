"""issuer_file — an issuer's own daily holdings file through rep1's parsers
(services.replica_parsers). One provider instance per active rep1 source key
(today: ssga_spdr — State Street SPDR xlsx; Invesco and iShares are rep1
sources but inactive: Invesco answers 406, iShares is bot-gated)."""

from services.security_data.providers.holdings_common import HoldingsProvider


class IssuerFileProvider(HoldingsProvider):
    pass
