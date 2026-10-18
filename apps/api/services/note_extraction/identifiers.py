"""Security identifiers a REVIEWER types — CUSIP and ISIN (bulkfetch Task 9).

The extraction side already refuses a CUSIP whose check digit fails
(``rules.find_cusip`` / ``edgar_pipeline.is_valid_cusip``); the gold review
path had no check at all, so a typo went straight into the gold set. This is
the review path's validator, reusing the SAME check-digit functions (no second
implementation):

  * CUSIP: exactly 9 characters and a valid modulus-10 check digit, or the
    write is REFUSED with a message naming what is wrong.
  * ISIN: exactly 12 characters, valid Luhn check digit, or REFUSED. A US/CA
    ISIN embeds the CUSIP at positions 3-11; when the note's CUSIP is known and
    the ISIN does not contain it, that is a WARNING plus a needs_review reason —
    the reviewer's value is still saved (the filing itself may be wrong).
"""
from __future__ import annotations

from services.edgar_pipeline import cusip_check_digit, is_valid_cusip
from services.note_extraction.rules import _isin_valid

CUSIP_COUNTRIES = ("US", "CA")


class IdentifierError(ValueError):
    """A reviewer value that is not a valid identifier — refuse the write."""


def clean(value) -> str:
    return "".join(str(value or "").split()).upper()


def check_cusip(value) -> str:
    """The canonical (upper-case, no spaces) CUSIP, or IdentifierError."""
    v = clean(value)
    if len(v) != 9:
        raise IdentifierError(f"a CUSIP is exactly 9 characters; {v!r} has {len(v)}")
    if not is_valid_cusip(v):
        try:
            want = cusip_check_digit(v[:8])
            hint = f" (the check digit for {v[:8]} is {want}, not {v[8]})"
        except ValueError:
            hint = " (it contains a character a CUSIP cannot)"
        raise IdentifierError(f"CUSIP {v} fails the check digit{hint}")
    return v


def check_isin(value, note_cusip: str | None = None) -> tuple[str, list[str]]:
    """``(canonical ISIN, warnings)``. Refuses a malformed ISIN; a US/CA ISIN
    that does not carry the note's CUSIP is a warning, not a refusal."""
    v = clean(value)
    if len(v) != 12:
        raise IdentifierError(f"an ISIN is exactly 12 characters; {v!r} has {len(v)}")
    if not _isin_valid(v):
        raise IdentifierError(f"ISIN {v} fails the check digit (or is not 2 letters + 9 alphanumerics + 1 digit)")
    warnings: list[str] = []
    cusip = clean(note_cusip) if note_cusip else ""
    if v[:2] in CUSIP_COUNTRIES and cusip:
        if v[2:11] != cusip:
            warnings.append(f"ISIN {v} does not contain the note's CUSIP {cusip} "
                            f"(positions 3-11 are {v[2:11]})")
    return v, warnings
