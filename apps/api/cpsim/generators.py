"""cpsim01 Task 4 — fake credentials and FAKE bank details, each provably NOT a real value.

Every generator here produces a value that the database (docs/cpsim01_part1.sql) re-checks independently:

* API key        ``cpsim_live_`` + 40 hex (160 bits). Shown once; stored as sha256(salt ‖ key) + last four.
* FIX password   ``cpsim_fixpw_`` + 24 hex. Same storage.
* ABA routing    ``99`` + 6 random digits + a check digit chosen to be WRONG. Two independent reasons it can never be
                 real: the Federal Reserve never assigns a ``99`` prefix (assigned: 00–12, 21–32, 61–72, 80), and the
                 real check-digit rule fails.
* Account        ``CPSIM`` + 10 digits — no bank issues a number with a letter prefix like this.
* BIC            ``CPSM`` + country ``XX`` (ISO 3166 user-assigned: no country, so no real BIC) + a location code whose
                 second character is ``0`` (SWIFT's test-and-training marker) [+ branch ``XXX``].
* IBAN           ``GB`` + check digits ``00`` / ``01`` / ``99`` + ``CPSM`` + 14 digits. ISO 13616 only ever produces
                 check digits 02–98, and the one of the three congruent (mod 97) to the canonical check is never
                 chosen — so the IBAN is invalid BY CONSTRUCTION on both rules (mod-97 ≠ 1, asserted).

Pure: stdlib only. ``rng`` is injectable for tests (defaults to ``secrets.SystemRandom()``).
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import string

KEY_PREFIX = "cpsim_live_"
FIX_PASSWORD_PREFIX = "cpsim_fixpw_"
SECRET_PREFIX = "cpsim_"            # every simulator credential starts with this; anything else is refused
ABA_PREFIX = "99"
ACCOUNT_PREFIX = "CPSIM"
BIC_BANK = "CPSM"
BIC_COUNTRY = "XX"
IBAN_COUNTRY = "GB"
IBAN_BANK = "CPSM"
INVALID_IBAN_CHECKS = ("00", "01", "99")
BANK_NAME_PREFIX = "CPSIM "

_RNG = secrets.SystemRandom()
_SLUG_RX = re.compile(r"[^a-z0-9]+")


# ── credentials ─────────────────────────────────────────────────────────────────────────────────────────────────
def hash_secret(salt: str, value: str) -> str:
    return hashlib.sha256((salt + value).encode("utf-8")).hexdigest()


def new_secret(prefix: str = KEY_PREFIX, nbytes: int = 20) -> dict:
    """A fresh simulator secret: ``{"value", "prefix", "salt", "hash", "last4"}``. ``value`` is returned to the creator
    ONCE and never stored or logged."""
    if not prefix.startswith(SECRET_PREFIX) or not re.fullmatch(r"cpsim_[a-z]+_", prefix):
        raise ValueError("a simulator secret prefix must look like cpsim_<word>_")
    body = secrets.token_hex(nbytes)
    value = prefix + body
    salt = secrets.token_hex(16)
    return {"value": value, "prefix": prefix, "salt": salt, "hash": hash_secret(salt, value), "last4": body[-4:]}


def new_api_key() -> dict:
    return new_secret(KEY_PREFIX, 20)


def new_fix_password() -> dict:
    return new_secret(FIX_PASSWORD_PREFIX, 12)


def is_simulator_secret(value: str | None) -> bool:
    return isinstance(value, str) and value.startswith(SECRET_PREFIX) and len(value) <= 200


def matches(salt: str, stored_hash: str, presented: str) -> bool:
    """Constant-time comparison of a presented secret against a stored salted hash."""
    return hmac.compare_digest(hash_secret(salt, presented), stored_hash)


# ── ABA routing numbers ─────────────────────────────────────────────────────────────────────────────────────────
_ABA_WEIGHTS = (3, 7, 1, 3, 7, 1, 3, 7, 1)


def aba_checksum_ok(r: str | None) -> bool:
    """The REAL ABA rule: 3·(d1+d4+d7) + 7·(d2+d5+d8) + (d3+d6+d9) ≡ 0 (mod 10)."""
    if not isinstance(r, str) or not re.fullmatch(r"[0-9]{9}", r):
        return False
    return sum(int(d) * w for d, w in zip(r, _ABA_WEIGHTS)) % 10 == 0


def fake_aba(rng=None) -> str:
    rng = rng or _RNG
    head = ABA_PREFIX + "".join(str(rng.randrange(10)) for _ in range(6))
    partial = sum(int(d) * w for d, w in zip(head, _ABA_WEIGHTS[:8]))
    correct = (-partial) % 10                    # the one digit that would make it pass
    wrong = (correct + 1 + rng.randrange(9)) % 10   # any of the nine others
    out = head + str(wrong)
    assert not aba_checksum_ok(out)
    return out


def fake_account_number(rng=None) -> str:
    rng = rng or _RNG
    return ACCOUNT_PREFIX + "".join(str(rng.randrange(10)) for _ in range(10))


# ── BIC / IBAN ──────────────────────────────────────────────────────────────────────────────────────────────────
def fake_bic(rng=None, branch: bool = True) -> str:
    rng = rng or _RNG
    loc = rng.choice(string.ascii_uppercase) + "0"
    return BIC_BANK + BIC_COUNTRY + loc + ("XXX" if branch else "")


def iban_ok(iban: str | None) -> bool:
    """The REAL IBAN rule (ISO 13616): first four characters to the end, letters → 10..35, mod 97 = 1."""
    if not isinstance(iban, str) or not re.fullmatch(r"[A-Z]{2}[0-9]{2}[A-Z0-9]{1,30}", iban):
        return False
    s = iban[4:] + iban[:4]
    digits = "".join(str(ord(c) - 55) if c.isalpha() else c for c in s)
    return int(digits) % 97 == 1


def _canonical_check(country: str, bban: str) -> int:
    s = bban + country + "00"
    digits = "".join(str(ord(c) - 55) if c.isalpha() else c for c in s)
    return 98 - int(digits) % 97


def fake_iban(rng=None) -> str:
    """Check digits from 00 / 01 / 99 — outside the 02–98 the algorithm produces — AND never the one of them that is
    congruent (mod 97) to the canonical check (00 ≡ 97, 01 ≡ 98, 99 ≡ 02), so the mod-97 test always fails too."""
    rng = rng or _RNG
    bban = IBAN_BANK + "".join(str(rng.randrange(10)) for _ in range(14))
    canon = _canonical_check(IBAN_COUNTRY, bban)
    choices = [c for c in INVALID_IBAN_CHECKS if int(c) % 97 != canon % 97]
    out = IBAN_COUNTRY + rng.choice(choices) + bban
    assert not iban_ok(out)
    return out


def fake_bank_details(kind: str, counterparty_name: str, rng=None) -> dict:
    """A complete fake bank-details row for ``kind`` ('domestic' | 'international')."""
    rng = rng or _RNG
    short = (counterparty_name or "Simulated").strip()[:60]
    if kind == "domestic":
        return {"kind": "domestic", "bank_name": BANK_NAME_PREFIX + short, "aba_wire": fake_aba(rng),
                "aba_ach": fake_aba(rng), "account_number": fake_account_number(rng), "bic": None, "iban": None,
                "intermediary_bank_name": None, "intermediary_bic": None, "intermediary_aba": None,
                "beneficiary_name": f"{short} (simulated)", "beneficiary_address": "1 Simulation Way, Nowhere"}
    if kind == "international":
        return {"kind": "international", "bank_name": BANK_NAME_PREFIX + short, "aba_wire": None, "aba_ach": None,
                "account_number": fake_account_number(rng), "bic": fake_bic(rng), "iban": fake_iban(rng),
                "intermediary_bank_name": BANK_NAME_PREFIX + "Intermediary", "intermediary_bic": fake_bic(rng),
                "intermediary_aba": fake_aba(rng), "beneficiary_name": f"{short} (simulated)",
                "beneficiary_address": "1 Simulation Way, Nowhere"}
    raise ValueError(f"unknown bank-details kind {kind!r}")


# ── slugs ───────────────────────────────────────────────────────────────────────────────────────────────────────
def slugify(name: str) -> str:
    s = _SLUG_RX.sub("-", (name or "").lower()).strip("-")[:48]
    if len(s) < 2:
        s = "counterparty"
    return s
