"""Deterministic cleanup of ``raw_underlying_text``. No model call.

WHAT THIS IS FOR
──────────────────────────────────────────────────────────────────────────────
The 97 unresolved edges in ``portfolio.securities_global_relationships`` carry
the underlying's name exactly as the prospectus wrote it. Prospectuses are
typeset documents, and the strings inherit typesetting noise: a superscript ®
lands as a free-floating character, an article survives from the sentence the
phrase was lifted out of, a ticker gloss trails in parentheses. That noise makes
eighteen references to the S&P 500 look like six different securities.

This module removes the noise and NOTHING ELSE. It does not decide what a string
refers to — that is :mod:`services.underlying_index_registry`, and it is a
lookup, not a similarity score. Keeping the two apart matters: normalization is
allowed to be aggressive because it is reversible (the raw text is never
overwritten), whereas matching is not allowed to guess at all.

CALIBRATED AGAINST THE REAL 57 STRINGS, NOT AGAINST A GUESS
──────────────────────────────────────────────────────────────────────────────
Every rule below exists because a specific string in the live corpus needs it.
Two of the five are for patterns the sprint brief did not list, and were found
by pulling the full distinct set rather than the top fifteen:

    'the Dow Jones Industrial Average ® (the "INDU Index")'  trailing gloss
    'S&P ® /ASX 200 Index'                                   space before '/'
    'Swiss Market Index (SMI ® )'                            space before ')'

WHAT IT DELIBERATELY DOES NOT COLLAPSE
──────────────────────────────────────────────────────────────────────────────
Only formatting is removed; every word survives. So these stay distinct, which
is the point — they are different securities with different (and in two cases
much worse) price series:

    'S&P 500 Index'  vs  'S&P 500 Futures Excess Return Index'
    'Nasdaq-100 Index'  vs  'Nasdaq-100 Equal Weighted Index'
                        vs  'Nasdaq-100 Technology Sector Index'
    'Russell 2000 Index'  vs  'iShares Russell 2000 ETF'

Casing is preserved apart from the leading article, per the brief. That means
'the Common Stock of NVIDIA Corporation' and 'the common stock of NVIDIA
Corporation' (both present in the corpus, 4 and 1 edges) normalize to strings
that differ in one character. That is intentional here and handled one layer up:
the registry lookup and the single-name pattern are both case-insensitive. A
normalizer that lower-cased would destroy 'EURO STOXX 50' and 'TOPIX', which are
the only signal distinguishing a mark from a word in some of these names.
"""

from __future__ import annotations

import re

# ── The symbols, and why 'SM' is treated differently from the rest ────────────
#
# ®, ™ and ℠ are single characters that can only ever be marks, so they are
# removed anywhere they appear.
#
# 'SM' is two ordinary letters. In the corpus it is a service mark set as
# superscript that flattened into the text — 'Dow Jones Industrial Average SM',
# 'Nasdaq-100 ® Technology Sector Index SM'. But 'SM' is also the first two
# letters of 'SMI' (the Swiss Market Index abbreviation, also in the corpus), so
# a blanket strip would corrupt a real name. It is therefore removed ONLY as a
# whole uppercase token at the very end of the string, where a mark is the only
# thing it can be. If an index ever legitimately ends in the word "SM", this
# rule is wrong — no such index exists, and narrowing it here is cheaper than
# discovering the false positive later.
_MARK_CHARS = re.compile(r"[®™℠]")          # ® ™ ℠
_TRAILING_SM = re.compile(r"(?:\s+|(?<=[a-z0-9]))SM\s*$")
_LEADING_ARTICLE = re.compile(r"^\s*the\s+", re.IGNORECASE)

# A trailing parenthetical is, in this corpus and without exception, a gloss on
# the name just given: (the "NDX Index"), (ticker: "NDX"), (SPXFP), (SPXF40D4),
# (SMI ® ). It never narrows the reference — the name before it is already
# complete — so keeping it only splits one security into several. Anchored to
# the END of the string on purpose: a mid-string parenthetical (none today) could
# be load-bearing, and this rule makes no claim about those.
_TRAILING_PAREN = re.compile(r"\s*\([^()]*\)\s*$")

# Removing a free-floating mark leaves the space that preceded it stranded in
# front of punctuation: 'S&P ® /ASX 200' -> 'S&P /ASX 200'. Closing that gap is
# part of undoing the typesetting, not a separate opinion about the name.
_SPACE_BEFORE_PUNCT = re.compile(r"\s+([/,.;:)\]])")
_SPACE_AFTER_OPEN = re.compile(r"([(\[/])\s+")
_WHITESPACE = re.compile(r"\s+")


def normalize_underlying_text(raw: str) -> str:
    """Strip typesetting noise from one ``raw_underlying_text`` value.

    Order matters and is not arbitrary:

    1. trailing parenthetical gloss — done FIRST, so a mark hiding inside it
       ('(SMI ® )') goes with it rather than being stripped and leaving '(SMI)'
       behind to be stripped separately.
    2. mark characters, then a trailing 'SM' token — 'SM' second, because
       'Nasdaq-100 ® Technology Sector Index SM' only ends in a bare 'SM' once
       the ® earlier in the string is gone and the tail is reachable.
    3. punctuation spacing, then whitespace collapse — spacing first, because it
       looks for the multi-space runs that step 2 creates.
    4. the leading article LAST, so 'The Common Stock of ...' is matched after
       any leading whitespace has been dealt with.

    Returns the cleaned string. Never returns ``None``; a blank or ``None``
    input returns ``''`` so a caller can treat "nothing to match" uniformly
    instead of branching on a type.
    """
    if not raw:
        return ""

    text = str(raw)
    text = _TRAILING_PAREN.sub("", text)
    text = _MARK_CHARS.sub(" ", text)
    text = _TRAILING_SM.sub("", text)
    text = _SPACE_BEFORE_PUNCT.sub(r"\1", text)
    text = _SPACE_AFTER_OPEN.sub(r"\1", text)
    text = _WHITESPACE.sub(" ", text).strip()
    text = _LEADING_ARTICLE.sub("", text)
    return text.strip()


def normalization_key(raw: str) -> str:
    """The case-insensitive form used for LOOKUP, not for display.

    :func:`normalize_underlying_text` preserves casing because the normalized
    string is shown to a reviewer and 'EURO STOXX 50' should not become 'euro
    stoxx 50' on screen. Matching, though, has to survive the corpus's genuine
    casing drift ('the Common Stock of NVIDIA' vs 'the common stock of NVIDIA').
    So every lookup goes through this, and only through this.

    ``casefold`` rather than ``lower``: it is the correct operation for
    case-insensitive comparison, and index names include non-ASCII issuers often
    enough that the distinction will eventually matter.
    """
    return normalize_underlying_text(raw).casefold()
