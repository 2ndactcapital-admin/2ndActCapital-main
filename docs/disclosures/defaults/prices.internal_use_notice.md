---
key: prices.internal_use_notice
title: Price data — internal use only
category: licence
audience: staff
status: default — have counsel review
---
<!-- summary -->
End-of-day prices from the free price source are licensed for internal use only: they support adviser analysis and are never shown to members. Until the operator's backfill has run and the prices job is on, price coverage stays 'unknown' and holdings are valued at their entered market value as of their statement date.
<!-- /summary -->

# Price data — internal use only

*Default — have counsel review.*

The platform's free end-of-day price source (today Yahoo Finance) is used under an internal-use assumption recorded by
the firm. Its terms do not allow display to members or redistribution. The data layer enforces this: a reader that
asks for prices to show a member is refused for this source.

- **Internal use**: tracking error, backtests, harvest scans, viability and block pricing.
- **Not allowed**: showing these prices to members, exporting them, or sending them to a third party.
- A licensed vendor (recorded with member display allowed) is required before members see prices from the platform.
