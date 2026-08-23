"""The closed set of indices this platform will match automatically.

A HAND-MAINTAINED TABLE, NOT A MATCHER
──────────────────────────────────────────────────────────────────────────────
There is no fuzzy matching in this module and there is not going to be. Lookup
is exact equality on the case-folded output of
:func:`services.underlying_normalization.normalize_underlying_text`. A string
either IS one of the names below or it is not, and "not" means a human looks at
it. That is the whole design.

The reason is the same one that made the note-terms corpus tractable: the
population is concentrated and the tail is thin. Across the live 97 unresolved
edges there are 37 distinct normalized names, and five index families account
for 57 of the edges. Hand-writing five to thirteen entries buys most of the
corpus at zero risk. A similarity model would buy the last few percent and
introduce a class of error — confidently wrong — that this pipeline has no way
to detect and a reviewer has no reason to suspect.

Adding an index is editing this file. That is a feature. Every automatic
resolution in the system traces to a line somebody wrote on purpose.

THE ENTRIES THAT LOOK REDUNDANT AND ARE NOT
──────────────────────────────────────────────────────────────────────────────
S&P 500 Index and S&P 500 Futures Excess Return Index are SEPARATE entries.
They are separate indices. The excess-return series is a futures-based total
return net of a financing cost, so it drifts persistently below the spot index —
collapsing them would not be a formatting simplification, it would silently
replace one security's price history with a materially worse one. Same reasoning
splits Nasdaq-100 from Nasdaq-100 Equal Weighted and Nasdaq-100 Technology
Sector: shared branding, different constituents, different returns.

Conversely TOPIX and the Tokyo Stock Price Index ARE one index under two names,
so they are two aliases pointing at one canonical entry. The alias mechanism
exists for that case and only that case — a name the issuer actually uses for
the same series, not a name that merely looks similar.

WHAT IS DELIBERATELY ABSENT
──────────────────────────────────────────────────────────────────────────────
* Single-name equities. 'Common Stock of NVIDIA Corporation' -> NVDA is obvious
  to a person and genuinely unsafe for a table lookup at scale: share classes,
  ticker reassignment after delisting, and foreign private issuers all break it.
  Those route to review.
* ETFs and funds (the SPDR sector funds, iShares, VanEck in this corpus). An ETF
  is a fund tracking an index, not the index; resolving one to the other would
  be wrong, and resolving it to itself needs a ticker, which is the single-name
  problem again.
* Decrement and risk-control indices (MerQube Vol Advantage, S&P 500 Futures 40%
  Intraday 4% Decrement VT, GS Momentum Builder Focus ER). These are bank-
  sponsored, usually have no public price series and often no ticker at all.
  They can still get a placeholder ``securities_global`` row — but through the
  reviewer's ``create_new`` on the confirm endpoint, where a person is asserting
  the security exists, not through a table that asserts it on their behalf.
"""

from __future__ import annotations

from typing import Any

from services.underlying_normalization import normalization_key

TABLE = "portfolio.securities_global"

# Every row this module creates. 'unknown' rather than 'no_public_source'
# because coverage is genuinely unknown until a feed sprint looks: these five
# families all HAVE public series, nothing has been wired to fetch them yet, and
# recording a guess as a fact is how a later sprint ends up skipping them.
PRICE_COVERAGE = "unknown"
SECURITY_TYPE = "index"


# ── The registry ─────────────────────────────────────────────────────────────
#
# Keyed by the CANONICAL normalized name. ``aliases`` lists other normalized
# names that denote the same index. ``ticker`` is the common market symbol,
# written to securities_global_identifiers so a later price sprint has a handle;
# none of these indices has a CUSIP, which is why the column is absent.
#
# Tier 1 — the five families the sprint brief names, plus the excess-return
# variant it explicitly requires be kept apart. 61 of the 97 edges.
# Tier 2 — the remaining names in the live corpus that are unambiguous,
# exchange-published indices with real public series. 8 more edges. Same rule
# applies: exact name or nothing.

_ENTRIES: list[dict[str, Any]] = [
    # ── Tier 1 ───────────────────────────────────────────────────────────────
    {
        "canonical": "S&P 500 Index",
        "short_name": "S&P 500",
        "ticker": "SPX",
        "currency_code": "USD",
        "aliases": [],
    },
    {
        "canonical": "Russell 2000 Index",
        "short_name": "Russell 2000",
        "ticker": "RTY",
        "currency_code": "USD",
        "aliases": [],
    },
    {
        "canonical": "Nasdaq-100 Index",
        "short_name": "Nasdaq-100",
        "ticker": "NDX",
        "currency_code": "USD",
        "aliases": [],
    },
    {
        "canonical": "Dow Jones Industrial Average",
        "short_name": "Dow Jones Industrial Average",
        "ticker": "INDU",
        "currency_code": "USD",
        # Note: no 'Index' suffix anywhere in the corpus for this one, hence no
        # alias for a form nobody writes.
        "aliases": [],
    },
    {
        "canonical": "EURO STOXX 50 Index",
        "short_name": "EURO STOXX 50",
        "ticker": "SX5E",
        "currency_code": "EUR",
        "aliases": [],
    },
    {
        # SEPARATE from S&P 500 Index, on purpose. See the module docstring.
        "canonical": "S&P 500 Futures Excess Return Index",
        "short_name": "S&P 500 Futures ER",
        "ticker": "SPXFP",
        "currency_code": "USD",
        "aliases": [],
    },

    # ── Tier 2 ───────────────────────────────────────────────────────────────
    {
        "canonical": "MSCI EAFE Index",
        "short_name": "MSCI EAFE",
        "ticker": "MXEA",
        "currency_code": "USD",
        "aliases": [],
    },
    {
        "canonical": "FTSE 100 Index",
        "short_name": "FTSE 100",
        "ticker": "UKX",
        "currency_code": "GBP",
        "aliases": [],
    },
    {
        "canonical": "S&P/ASX 200 Index",
        "short_name": "S&P/ASX 200",
        "ticker": "AS51",
        "currency_code": "AUD",
        "aliases": [],
    },
    {
        "canonical": "Swiss Market Index",
        "short_name": "SMI",
        "ticker": "SMI",
        "currency_code": "CHF",
        "aliases": [],
    },
    {
        # Two names, one index. The corpus contains both, once each.
        "canonical": "TOPIX Index",
        "short_name": "TOPIX",
        "ticker": "TPX",
        "currency_code": "JPY",
        "aliases": ["Tokyo Stock Price Index"],
    },
    {
        # Equal-weighted NDX. Different constituent weights, different series.
        "canonical": "Nasdaq-100 Equal Weighted Index",
        "short_name": "Nasdaq-100 Equal Weighted",
        "ticker": "NDXE",
        "currency_code": "USD",
        "aliases": [],
    },
    {
        # A sector sub-index of NDX, not NDX.
        "canonical": "Nasdaq-100 Technology Sector Index",
        "short_name": "Nasdaq-100 Technology Sector",
        "ticker": "NDXT",
        "currency_code": "USD",
        "aliases": [],
    },
]


def _build() -> dict[str, dict[str, Any]]:
    """Flatten canonical names and aliases into one case-folded lookup.

    Raises on a duplicate key rather than letting the last definition win. Two
    entries claiming the same name is an editing mistake in this file, and a
    silent overwrite would resolve some edges to the wrong index with no trace.
    """
    table: dict[str, dict[str, Any]] = {}
    for entry in _ENTRIES:
        record = {
            "name": entry["canonical"],
            "short_name": entry["short_name"],
            "ticker": entry["ticker"],
            "currency_code": entry["currency_code"],
            "security_type": SECURITY_TYPE,
            "price_coverage": PRICE_COVERAGE,
        }
        for name in [entry["canonical"], *entry["aliases"]]:
            key = normalization_key(name)
            if key in table and table[key]["name"] != record["name"]:
                raise ValueError(
                    f"underlying_index_registry: {name!r} is claimed by both "
                    f"{table[key]['name']!r} and {record['name']!r}"
                )
            table[key] = record
    return table


#: normalized (case-folded) name -> the securities_global row it denotes.
KNOWN_INDICES: dict[str, dict[str, Any]] = _build()


def lookup_index(normalized_name: str) -> dict[str, Any] | None:
    """The registry entry for a normalized name, or ``None``.

    Exact, case-insensitive equality. ``None`` is the correct and expected
    answer for most of the tail — it means "a person decides", not "failure".
    """
    if not normalized_name:
        return None
    return KNOWN_INDICES.get(normalization_key(normalized_name))


async def resolve_or_create_index_security(pool, normalized_name: str) -> str:
    """The ``securities_global.id`` for a known index, creating the row if new.

    Safe to call twice. Three things make that true, in increasing order of
    how much you should trust them:

    1. The SELECT-then-INSERT runs inside one transaction.
    2. ``uq_sec_global_active_index_name`` (added in this sprint's Part 1 SQL —
       the table previously had NO unique constraint of any kind) makes a losing
       concurrent INSERT raise instead of succeeding.
    3. That raise is caught and the SELECT re-run, so the loser returns the
       winner's id rather than an error. Serialising on the database is the only
       version of "idempotent" that survives two workers.

    Raises ``KeyError`` for a name that is not in the registry. This function
    creates rows, and it will not create one for a string nobody vetted — that
    is precisely the auto-resolution the sprint forbids. Callers check
    :func:`lookup_index` first.
    """
    entry = lookup_index(normalized_name)
    if entry is None:
        raise KeyError(
            f"{normalized_name!r} is not in KNOWN_INDICES; "
            "resolve_or_create_index_security does not invent securities"
        )

    async with pool.acquire() as conn:
        async with conn.transaction():
            # RLS on securities_global gates INSERT on this GUC. Set LOCAL, so
            # it expires with the transaction and cannot leak to the next
            # statement on a pooled connection.
            await conn.execute(
                "SELECT set_config('app.is_super_admin', 'true', true)"
            )

            existing = await _select_index_id(conn, entry["name"])
            if existing is not None:
                return existing

            try:
                new_id = await conn.fetchval(
                    f"""
                    INSERT INTO {TABLE}
                        (name, short_name, security_type, currency_code,
                         price_coverage)
                    VALUES ($1, $2, $3, $4, $5)
                    RETURNING id
                    """,
                    entry["name"], entry["short_name"], entry["security_type"],
                    entry["currency_code"], entry["price_coverage"],
                )
            except Exception:  # noqa: BLE001 — unique violation OR anything else
                # Re-read before deciding this was a real failure. If a
                # concurrent caller just created the row, we want its id, not an
                # exception; if it did not, there is nothing to find and the
                # original error is re-raised with its own context intact.
                found = await _select_index_id(conn, entry["name"])
                if found is not None:
                    return found
                raise

            await _write_ticker_identifier(conn, new_id, entry["ticker"])
            return str(new_id)


async def _select_index_id(conn, name: str) -> str | None:
    """The current index row for this exact name, case-insensitively.

    Matches the predicate of ``uq_sec_global_active_index_name`` exactly —
    ``lower(name)`` restricted to current index rows — so the lookup and the
    constraint can never disagree about what counts as a duplicate.
    """
    found = await conn.fetchval(
        f"""
        SELECT id FROM {TABLE}
        WHERE lower(name) = lower($1)
          AND security_type = '{SECURITY_TYPE}'
          AND valid_to IS NULL AND system_to IS NULL
        """,
        name,
    )
    return str(found) if found else None


async def _write_ticker_identifier(conn, security_id, ticker: str) -> None:
    """Record the market symbol so a later price sprint has something to fetch on.

    Written as ``id_type='ticker'``, which is outside
    ``uq_sec_global_ident_issuer_assigned`` (cusip/isin/figi/lei only) — an index
    symbol is not issuer-assigned and 'SMI' is legitimately reused across
    venues. Guarded by an explicit existence check instead, and never fatal: a
    missing identifier row is a gap for a later sprint to fill, not a reason to
    fail a resolution that is otherwise correct.
    """
    if not ticker:
        return
    already = await conn.fetchval(
        """
        SELECT 1 FROM portfolio.securities_global_identifiers
        WHERE global_security_id = $1::uuid AND id_type = 'ticker'
          AND id_value = $2 AND valid_to IS NULL AND system_to IS NULL
        """,
        str(security_id), ticker,
    )
    if already:
        return
    await conn.execute(
        """
        INSERT INTO portfolio.securities_global_identifiers
            (global_security_id, id_type, id_value, is_primary)
        VALUES ($1::uuid, 'ticker', $2, true)
        """,
        str(security_id), ticker,
    )
