"""SECMASTER1 — symbols, exchanges, security types and check digits. Pure: no DB, no network.

ONE CANONICAL SYMBOL FORM (Task 1d)
───────────────────────────────────
The five sources spell the same listing five ways:

    Nasdaq Trader ACT   BRK.B   ASB$E   AAC.W    AAC.U   AIIA.R
    Nasdaq Trader CQS   BRK.B   ASBpE   AAC.WS   AAC.U   AIIAr
    SEC / Yahoo         BRK-B   ASB-PE  AAC-WT   AAC-UN  AIIA-RI
    OpenFIGI (BBG)      BRK/B

The stored ticker (securities_global_identifiers id_type 'ticker') is the dotted form:

    class shares   BRK.B          preferred    ASB.PRE (series E; bare preferred ASB.PR)
    warrants       AAC.WS         units        AAC.U          rights   AIIA.R

Nasdaq's own fifth-letter suffixes (AACIU, AACIW, ABCDR) are the real Nasdaq tickers and are kept as given.
``normalize_symbol`` is idempotent on the canonical form, and ``securities_global.normalize_identifier_value``
applies it to every ticker write AND lookup, so "BRK-B", "BRK/B" and "brk.b" all find BRK.B.

EXCHANGES are stored as MIC codes (XNAS, XNYS, XASE, ARCX, BATS, IEXG, XCHI, TXSE, OOTC). Matching on
ticker + exchange compares the exchange FAMILY (nasdaq / nyse / cboe / iex / txse / otc): the SEC files say
"NYSE" for a NYSE American listing that Nasdaq Trader codes 'A'. OpenFIGI's US composite ('US') matches any US
family except OTC.
"""

from __future__ import annotations

import re

# ── symbols ─────────────────────────────────────────────────────────────────
_WARRANT = {"W", "WS", "WT", "WTS", "+"}
_UNIT = {"U", "UN", "UNIT", "="}
_RIGHT = {"R", "RT", "RI", "RTS", "^"}


def _suffix(base: str, suf: str) -> str:
    suf = suf.strip(".-/ ")
    if not suf:
        return base
    if suf in _WARRANT:
        return f"{base}.WS"
    if suf in _UNIT:
        return f"{base}.U"
    if suf in _RIGHT:
        return f"{base}.R"
    m = re.fullmatch(r"(?:PR|P)([A-Z]{0,2})", suf)
    if m:
        return f"{base}.PR{m.group(1)}"
    return f"{base}.{suf}"


def normalize_symbol(raw: str | None) -> str | None:
    """Any source's spelling → the canonical dotted form. None / blank → None. Idempotent."""
    s = (raw or "").strip().upper().replace(" ", "")
    if not s:
        return None
    # Nasdaq ACT preferred: ASB$E / ASB$  (the '$' marks a preferred)
    if "$" in s:
        base, _, series = s.partition("$")
        return f"{base}.PR{series}"
    # CQS lower-case forms arrive upper-cased: ASBPE is ambiguous, so CQS symbols are never passed here —
    # the adapters use the ACT symbol. Explicit separators:
    for sep in (".", "-", "/"):
        if sep in s:
            base, _, suf = s.partition(sep)
            if not base:
                return s
            return _suffix(base, suf.replace(sep, ""))
    if s[-1] in "+=^":
        return _suffix(s[:-1], s[-1])
    return s


def to_openfigi_ticker(canonical: str) -> str | None:
    """The Bloomberg ticker OpenFIGI expects, for COMMON / CLASS shares and ETFs only. Preferreds, warrants,
    units and rights are not sent by ticker (Bloomberg spells them per issue); they are reached through their
    CUSIP (the 13(f) list) instead."""
    if not canonical:
        return None
    if "." not in canonical:
        return canonical
    base, _, suf = canonical.partition(".")
    if suf in ("WS", "U", "R") or suf.startswith("PR"):
        return None
    return f"{base}/{suf}"


def is_test_symbol(sym: str) -> bool:
    return bool(re.match(r"^(ZZZT|ZXZZT|ZVZZT|ZWZZT|ZBZZT|ZJZZT|ATEST|NTEST|CTEST|PTEST)", sym or ""))


# ── exchanges ───────────────────────────────────────────────────────────────
NASDAQ_LISTED_MIC = "XNAS"
OTHERLISTED_EXCHANGE = {"A": "XASE", "N": "XNYS", "P": "ARCX", "Z": "BATS", "V": "IEXG", "M": "XCHI", "F": "TXSE"}
SEC_EXCHANGE = {"NASDAQ": "XNAS", "NYSE": "XNYS", "CBOE": "BATS", "OTC": "OOTC", "TXSE": "TXSE"}
FD_EXCHANGE = {"NMS": "XNAS", "NGM": "XNAS", "NCM": "XNAS", "NAS": "XNAS", "NYQ": "XNYS", "NYS": "XNYS",
               "ASE": "XASE", "PCX": "ARCX", "BTS": "BATS", "PNK": "OOTC"}
FAMILY = {"XNAS": "nasdaq", "XNYS": "nyse", "XASE": "nyse", "ARCX": "nyse", "XCHI": "nyse", "BATS": "cboe",
          "IEXG": "iex", "TXSE": "txse", "OOTC": "otc", "US": "us"}
EXCHANGE_LABELS = {"XNAS": "Nasdaq", "XNYS": "NYSE", "XASE": "NYSE American", "ARCX": "NYSE Arca",
                   "BATS": "Cboe BZX", "IEXG": "IEX", "XCHI": "NYSE Texas (Chicago)", "TXSE": "Texas Stock Exchange",
                   "OOTC": "OTC", "US": "US composite"}


def sec_exchange_mic(raw: str | None) -> str | None:
    if not raw:
        return None
    return SEC_EXCHANGE.get(raw.strip().upper(), raw.strip().upper())


def family(mic: str | None) -> str | None:
    if not mic:
        return None
    return FAMILY.get(mic, mic.lower())


def families_compatible(a: str | None, b: str | None) -> bool:
    """Same exchange family; the US composite matches any US family but OTC; an unknown side never matches."""
    fa, fb = family(a), family(b)
    if fa is None or fb is None:
        return False
    if fa == fb:
        return True
    if "us" in (fa, fb):
        other = fb if fa == "us" else fa
        return other != "otc"
    return False


# ── security types (securities_global_type_chk) ─────────────────────────────
def guess_type_from_name(name: str, *, etf: bool) -> str:
    """A Nasdaq Trader row's type, from its ETF flag and security name. Conservative: when unsure, 'equity'."""
    if etf:
        return "fund"
    n = f" {(name or '').lower()} "
    if re.search(r"\bwarrants?\b", n) or re.search(r"\brights?\b", n) or re.search(r"\bunits?\b", n):
        return "other"
    if re.search(r"\b(notes?|debentures?|bonds?)\b( due)?", n) and "depositary" not in n:
        return "fixed_income"
    if re.search(r"\b(fund|trust etf|etn)\b", n):
        return "fund"
    return "equity"


_FIGI_TYPE = {"common stock": "equity", "preferred stock": "equity", "depositary receipt": "equity",
              "adr": "equity", "gdr": "equity", "reit": "equity", "mlp": "equity", "ltd part": "equity",
              "mutual fund": "fund", "etp": "fund", "closed-end fund": "fund", "open-end fund": "fund",
              "unit": "other", "warrant": "other", "right": "other", "option": "option", "index": "index"}


def type_from_figi(hit: dict | None) -> str | None:
    if not hit:
        return None
    for k in ("securityType2", "securityType"):
        v = (hit.get(k) or "").strip().lower()
        if v in _FIGI_TYPE:
            return _FIGI_TYPE[v]
    sector = (hit.get("marketSector") or "").strip().lower()
    if sector in ("corp", "govt", "muni", "mtge"):
        return "fixed_income"
    if sector == "index":
        return "index"
    if sector == "equity":
        return "equity"
    return None


# ── check digits ────────────────────────────────────────────────────────────
def _char_value(c: str) -> int:
    if c.isdigit():
        return int(c)
    if "A" <= c <= "Z":
        return ord(c) - ord("A") + 10
    return {"*": 36, "@": 37, "#": 38}[c]


def cusip_check_digit(first8: str) -> int:
    total = 0
    for i, c in enumerate(first8.upper()):
        v = _char_value(c)
        if i % 2 == 1:
            v *= 2
        total += v // 10 + v % 10
    return (10 - total % 10) % 10


def is_valid_cusip(cusip: str | None) -> bool:
    c = (cusip or "").strip().upper()
    if not re.fullmatch(r"[0-9A-Z*@#]{8}[0-9]", c):
        return False
    return cusip_check_digit(c[:8]) == int(c[8])


def isin_check_digit(body: str) -> int:
    """ISIN check digit for the 11-character body (country + NSIN): letters → 10..35, then Luhn from the right."""
    digits = "".join(str(_char_value(c)) for c in body.upper())
    total = 0
    for i, ch in enumerate(reversed(digits)):
        v = int(ch)
        if i % 2 == 0:
            v *= 2
        total += v // 10 + v % 10
    return (10 - total % 10) % 10


def isin_from_cusip(cusip: str | None, country: str = "US") -> str | None:
    """'US' + CUSIP + check digit, for a US-issuer CUSIP (first character a digit). A CINS (first character a
    letter: a non-US issuer) has a different country prefix and is NOT derived. A Canadian issuer's numeric CUSIP
    also carries a 'CA' ISIN — that cannot be told from the CUSIP alone; a source that states a different ISIN for
    the security raises an identifier_conflict review item rather than being overwritten."""
    c = (cusip or "").strip().upper()
    if not is_valid_cusip(c) or not c[0].isdigit():
        return None
    body = f"{country}{c}"
    return f"{body}{isin_check_digit(body)}"
