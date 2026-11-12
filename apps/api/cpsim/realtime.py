"""cpsim06 — the SIMULATED Realtime-API-shaped account opening (docs/cpsim/CPSIM06_NOTES.md D1–D3; every path and field
ASSUMED — the real Realtime API paths are not published).

Joe's rule (2026-11-12): "as long as all the data is provided and in good order (no obvious data issues), the account
gets 'opened', the response is generated and the open account appears in the feed — no need for a mock client to
approve". So ``submit`` validates and, with no problem, OPENS the account at once: status ACTIVE, ``date_open`` = the
counterparty clock, a fake number ``CPSIM`` + 9 digits, the account written to ``cpsim.book_accounts`` under its household
(served by the Open API ``/v2/accounts`` feed immediately). A refusal lists EVERY problem, field by field.

Token (password grant, Keycloak-shaped): access tokens 5 minutes; refresh tokens on a FIXED 10-hour window from the
session's first issue — a refresh never extends it, and the ORIGINAL refresh token stays valid.

Sensitive data (D3): a tax id is read from the request, checked, and stored as its last four + a salted sha256 (bytea)
— never the number. A date of birth is never stored: the contact keeps ``age_years`` at ``age_as_of`` (the clock).
Problems never echo a value. Nothing here prints or logs a request body.

Every function takes a connection already AS ``cpsim_app`` inside a transaction (cpsim.db.tx). Imports nothing from
services/ (the cpsim boundary).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from cpsim import oauth as O

# ── constants ───────────────────────────────────────────────────────────────────────────────────────────────────────
ACCESS_PREFIX = "cpsim_rtat_"
REFRESH_PREFIX = "cpsim_rtrt_"
PASSWORD_PREFIX = "cpsim_rtpw_"
ACCESS_TTL = timedelta(minutes=5)                 # published: access tokens 5 minutes
SESSION_TTL = timedelta(hours=10)                 # published: refresh valid 10 h from issue, FIXED window
GRANTS = ("password", "refresh_token")
READ_LIMIT, WRITE_LIMIT = 60, 30                  # published per-minute limits (headers only; the simulator does not meter)
MINOR_AGE = 18                                    # D2: 18 (some states use 21 — documented)
VISA_MIN_DAYS = 90                                # published: a listed visa must not expire within 90 days
VISA_TYPES = ("E1", "E2", "E3", "F1", "G4", "H1B", "L1", "O1", "TN1")
CITIZENSHIP = ("us_citizen", "permanent_resident", "visa")
EMPLOYMENT = ("employed", "self_employed", "retired", "unemployed", "student")
TAX_KINDS = ("ssn", "itin", "ein")
TIERS = ("primary", "contingent")
PERSON_ROLES = ("owner", "joint_owner", "trustee", "custodian", "minor")
ENTITY_ROLES = ("trust",)
US_STATES = frozenset((
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA "
    "RI SC SD TN TX UT VT VA WA WV WI WY DC").split())
US_COUNTRY = ("US", "USA", "UNITED STATES", "UNITED STATES OF AMERICA")
PO_BOX_RX = re.compile(r"\b(p\.?\s*o\.?\s*box|post\s+office\s+box|p\.?\s*o\.?\s*b\b|pob\b)", re.IGNORECASE)
ZIP_RX = re.compile(r"^(\d{5})(?:-?(\d{4}))?$")
EMAIL_RX = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[A-Za-z]{2,}$")
REF_RX = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}$")
#: IRS EIN prefixes never assigned (simplification — CPSIM06_NOTES §4 V10)
EIN_BAD_PREFIXES = frozenset({"00", "07", "08", "09", "17", "18", "19", "28", "29", "49", "69", "70", "78", "79", "89",
                              "96", "97"})
#: IRS ITIN group (digits 4–5) ranges
ITIN_GROUPS = tuple(range(50, 66)) + tuple(range(70, 89)) + (90, 91, 92) + tuple(range(94, 100))

#: the PUBLISHED YES list (keys ASSUMED) → (label, shape, the Open API feed's account_type)
ACCOUNT_TYPES = {
    "individual": ("Individual", "single", "INDIVIDUAL"),
    "individual_cash": ("Individual Cash", "single", "INDIVIDUAL CASH"),
    "jtwros": ("Joint Tenants With Right of Survivorship", "joint", "JTWROS"),
    "trust": ("Trust (revocable or irrevocable)", "trust", "TRUST"),
    "trust_cash": ("Trust Cash", "trust", "TRUST CASH"),
    "ira": ("IRA (Traditional)", "single", "IRA"),
    "roth_ira": ("Roth IRA", "single", "ROTH IRA"),
    "rollover_ira": ("Rollover IRA", "single", "ROLLOVER IRA"),
    "beneficiary_ira": ("Beneficiary IRA", "single", "BENEFICIARY IRA"),
    "simple_ira": ("SIMPLE IRA", "single", "SIMPLE IRA"),
    "solo_401k": ("Solo 401(k)", "single", "SOLO 401K"),
    "utma": ("UTMA", "custodial", "UTMA"),
    "ugma": ("UGMA", "custodial", "UGMA"),
    "minor_roth_ira": ("Minor Roth IRA", "custodial", "MINOR ROTH IRA"),
}
#: the PUBLISHED NO list (keys ASSUMED) → label
UNSUPPORTED_TYPES = {
    "joint_cash": "Joint Cash", "single_member_llc_cash": "Single-Member LLC Cash",
    "c_corporation_cash": "C Corporation Cash", "s_corporation_cash": "S Corporation Cash",
    "llc_s_corporation_cash": "LLC S Corporation Cash", "non_profit_cash": "Non-Profit Organization Cash",
    "sep_ira": "SEP IRA", "beneficiary_roth_ira": "Beneficiary Roth IRA", "roth_solo_401k": "Roth Solo 401(k)",
    "retirement_trust": "Retirement Trust", "joint_tenants_in_common": "Joint Tenants In Common",
    "joint_tenants_by_entirety": "Joint Tenants By the Entirety", "joint_community_property": "Joint Community Property",
    "minor_ira": "Minor IRA", "sole_proprietorship": "Sole Proprietorship", "single_member_llc": "Single-Member LLC",
    "c_corporation": "C Corporation", "llc_c_corporation": "LLC C Corporation", "s_corporation": "S Corporation",
    "llc_s_corporation": "LLC S Corporation", "partnership": "Partnership", "llc_partnership": "LLC Partnership",
    "non_profit": "Non-Profit Organization", "donor_advised_fund": "Donor-Advised Fund",
}
UNSUPPORTED_MESSAGE = ("not supported by the custodian's API for account opening — open it through the custodian's own "
                       "process")
#: shape → the roles an account of that shape takes: role → (min, max)
SHAPE_ROLES = {
    "single": {"owner": (1, 1)},
    "joint": {"owner": (1, 1), "joint_owner": (1, 1)},
    "trust": {"trust": (1, 1), "trustee": (1, None)},
    "custodial": {"minor": (1, 1), "custodian": (1, 1)},
}

# test injection: the next N Realtime API calls answer 429 + Retry-After (rate-limit proof; the simulator never meters)
_force_429 = {"count": 0, "retry_after": 1}


def force_429(count: int, retry_after: int = 1) -> None:
    _force_429["count"] = max(int(count), 0)
    _force_429["retry_after"] = max(int(retry_after), 0)


def take_429() -> int | None:
    """Retry-After seconds when this call is to be refused 429, else None (consumes one)."""
    if _force_429["count"] > 0:
        _force_429["count"] -= 1
        return _force_429["retry_after"]
    return None


def rate_headers(kind: str) -> dict:
    limit = WRITE_LIMIT if kind == "write" else READ_LIMIT
    return {"RateLimit-Limit": str(limit), "RateLimit-Remaining": str(limit - 1), "RateLimit-Reset": "60"}


class RealtimeError(Exception):
    """A refusal with an HTTP status and (optionally) the problem list."""

    def __init__(self, status: int, message: str, problems: list | None = None):
        super().__init__(message)
        self.status = status
        self.problems = problems or []


def problem(field: str, code: str, message: str) -> dict:
    return {"field": field, "code": code, "message": message}


def new_id(prefix: str) -> str:
    """``<prefix><16 hex>`` with no run of five digits — ids are copied into columns whose D3 CHECK refuses a 9-digit
    run, and a random hex string would contain one about one time in ten."""
    while True:
        h = secrets.token_hex(8)
        if not re.search(r"\d{5}", h):
            return prefix + h


def _safe(s: str | None) -> str | None:
    """Free text that could carry a full tax id by mistake is dropped (D3: never stored, never echoed)."""
    return None if s is None or re.search(r"\d{9}", s) else s


def _word(v, n: int = 20) -> str | None:
    """A short vocabulary word (tier / kind / role): lower-cased; anything carrying digits is 'invalid'."""
    s = (_txt(v, n) or "").lower() or None
    return "invalid" if s and re.search(r"\d", s) else s


# ── users (password grant) ──────────────────────────────────────────────────────────────────────────────────────────
def _pw_hash(salt: bytes, password: str) -> bytes:
    return hashlib.sha256(salt + password.encode("utf-8")).digest()


async def create_user(conn, counterparty_id, username: str, actor=None) -> dict:
    """A simulator user. The password is generated and returned ONCE; only a salted hash is stored."""
    username = (username or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{2,63}", username):
        raise RealtimeError(422, "username: 3–64 characters, lower-case letters, digits, '.', '_' or '-'")
    if await conn.fetchval("SELECT 1 FROM cpsim.realtime_users WHERE counterparty_id = $1::uuid AND username = $2 AND "
                           "revoked_at IS NULL", str(counterparty_id), username):
        raise RealtimeError(409, "a live simulator user with this username exists")
    password = PASSWORD_PREFIX + secrets.token_urlsafe(24)
    salt = os.urandom(16)
    uid = await conn.fetchval(
        "INSERT INTO cpsim.realtime_users (counterparty_id, username, password_salt, password_hash, created_by) VALUES "
        "($1::uuid, $2, $3, $4, $5::uuid) RETURNING id::text", str(counterparty_id), username, salt,
        _pw_hash(salt, password), str(actor) if actor else None)
    return {"id": uid, "username": username, "password": password}


async def revoke_user(conn, counterparty_id, user_id) -> bool:
    r = await conn.fetchval("UPDATE cpsim.realtime_users SET revoked_at = $3 WHERE id = $1::uuid AND counterparty_id = "
                            "$2::uuid AND revoked_at IS NULL RETURNING id", str(user_id), str(counterparty_id), O.now())
    if r is None:
        return False
    await conn.execute("UPDATE cpsim.realtime_tokens SET revoked_at = $2 WHERE user_id = $1::uuid AND revoked_at IS "
                       "NULL", str(user_id), O.now())
    return True


async def users(conn, counterparty_id) -> list[dict]:
    return [{"id": r["id"], "username": r["username"], "created_at": r["created_at"].isoformat(),
             "revoked_at": r["revoked_at"].isoformat() if r["revoked_at"] else None}
            for r in await conn.fetch("SELECT id::text, username, created_at, revoked_at FROM cpsim.realtime_users "
                                      "WHERE counterparty_id = $1::uuid ORDER BY created_at DESC", str(counterparty_id))]


# ── tokens ──────────────────────────────────────────────────────────────────────────────────────────────────────────
def _new_token(prefix: str) -> tuple[str, str, str]:
    body = secrets.token_hex(20)
    value = prefix + body
    return value, O.digest(value), body[-4:]


async def _issue(conn, cp: dict, user_id: str, session_id: str, session_end: datetime) -> dict:
    t = O.now()
    access, a_hash, a4 = _new_token(ACCESS_PREFIX)
    refresh, r_hash, r4 = _new_token(REFRESH_PREFIX)
    a_exp = min(t + ACCESS_TTL, session_end)
    sql = ("INSERT INTO cpsim.realtime_tokens (counterparty_id, user_id, session_id, kind, token_hash, last4, created_at, "
           "expires_at, session_expires_at) VALUES ($1::uuid, $2::uuid, $3::uuid, $4, $5, $6, $7, $8, $9)")
    await conn.execute(sql, str(cp["id"]), user_id, session_id, "access", a_hash, a4, t, a_exp, session_end)
    await conn.execute(sql, str(cp["id"]), user_id, session_id, "refresh", r_hash, r4, t, session_end, session_end)
    return {"access_token": access, "expires_in": int((a_exp - t).total_seconds()), "refresh_token": refresh,
            "refresh_expires_in": int((session_end - t).total_seconds()), "token_type": "Bearer",
            "not-before-policy": 0, "scope": "profile email"}


async def token(conn, cp: dict, form: dict) -> dict:
    """The password-grant token endpoint. Every check runs before any write (a refusal persists nothing)."""
    grant = form.get("grant_type")
    if grant not in GRANTS:
        raise O.OAuthRefused(400, "unsupported_grant_type", "bad_grant", "grant_type must be password or refresh_token")
    await O._authenticate_client(conn, cp, form)          # client_id + the cpsim_ client secret (cpsim02's client auth)
    t = O.now()
    if grant == "password":
        username = (form.get("username") or "").strip().lower()
        password = form.get("password") or ""
        row = await conn.fetchrow("SELECT id::text, password_salt, password_hash FROM cpsim.realtime_users WHERE "
                                  "counterparty_id = $1::uuid AND username = $2 AND revoked_at IS NULL",
                                  str(cp["id"]), username) if username else None
        if row is None or not hmac.compare_digest(bytes(row["password_hash"]),
                                                  _pw_hash(bytes(row["password_salt"]), password)):
            raise O.OAuthRefused(401, "invalid_grant", "bad_grant", "Invalid user credentials")
        return await _issue(conn, cp, row["id"], str(uuid.uuid4()), t + SESSION_TTL)
    rt = form.get("refresh_token") or ""
    row = await conn.fetchrow(
        "SELECT t.user_id::text, t.session_id::text, t.expires_at, t.session_expires_at, t.revoked_at, u.revoked_at AS "
        "user_revoked FROM cpsim.realtime_tokens t JOIN cpsim.realtime_users u ON u.id = t.user_id WHERE t.token_hash = "
        "$1 AND t.counterparty_id = $2::uuid AND t.kind = 'refresh'", O.digest(rt), str(cp["id"])) \
        if rt.startswith(REFRESH_PREFIX) else None
    if row is None:
        raise O.OAuthRefused(400, "invalid_grant", "token_unknown", "Invalid refresh token")
    if row["revoked_at"] is not None or row["user_revoked"] is not None:
        raise O.OAuthRefused(400, "invalid_grant", "token_revoked", "Token is not active")
    if row["expires_at"] <= t:
        raise O.OAuthRefused(400, "invalid_grant", "token_expired", "Token is not active (the session's 10-hour window "
                                                                    "has ended)")
    # FIXED window: the new tokens end with the SAME session; the presented refresh token is NOT rotated
    return await _issue(conn, cp, row["user_id"], row["session_id"], row["session_expires_at"])


async def check_access(conn, cp: dict, presented: str | None) -> str:
    """'ok' or the refusal reason for a Realtime resource call."""
    if presented is None:
        return "missing"
    if presented == "":
        return "malformed"
    if not presented.startswith(ACCESS_PREFIX):
        return "token_unknown"
    row = await conn.fetchrow(
        "SELECT t.expires_at, t.revoked_at, u.revoked_at AS user_revoked FROM cpsim.realtime_tokens t JOIN "
        "cpsim.realtime_users u ON u.id = t.user_id WHERE t.token_hash = $1 AND t.counterparty_id = $2::uuid AND "
        "t.kind = 'access'", O.digest(presented), str(cp["id"]))
    if row is None:
        return "token_unknown"
    if row["revoked_at"] is not None or row["user_revoked"] is not None:
        return "token_revoked"
    if row["expires_at"] <= O.now():
        return "token_expired"
    if cp["status"] != "active":
        return "disabled"
    return "ok"


# ── field checks (pure; problems never echo a value) ────────────────────────────────────────────────────────────────
def _txt(v, n: int = 200) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s[:n] if s else None


def check_address(a, path: str, *, residential: bool) -> tuple[dict | None, list[dict]]:
    """→ (the stored address, problems). Residential: US, no PO Box; both: US state / DC, ZIP 5 or 9 digits."""
    if a is None:
        return None, []
    if not isinstance(a, dict):
        return None, [problem(path, "invalid", "the address must be an object")]
    out, probs = {}, []
    for f in ("street1", "street2", "city", "state", "postal_code", "country"):
        out[f] = _txt(a.get(f), 120)
    for f in ("street1", "city", "state", "postal_code"):
        if not out[f]:
            probs.append(problem(f"{path}.{f}", "missing", f"{f.replace('_', ' ')} is required"))
    country = (out["country"] or "US").upper()
    if country not in US_COUNTRY:
        probs.append(problem(f"{path}.country", "foreign_address",
                             "must be a US address — foreign addresses are not accepted"))
    out["country"] = "US" if country in US_COUNTRY else None
    if residential and any(PO_BOX_RX.search(out[f] or "") for f in ("street1", "street2")):
        probs.append(problem(f"{path}.street1", "po_box",
                             "a residential address cannot be a PO Box — give the street address where the client lives"))
    if out["state"]:
        st = out["state"].upper()
        if st not in US_STATES:
            probs.append(problem(f"{path}.state", "bad_state", "state must be a US state or DC (two-letter code)"))
            out["state"] = None
        else:
            out["state"] = st
    if out["postal_code"]:
        m = ZIP_RX.match(out["postal_code"])
        if not m:
            probs.append(problem(f"{path}.postal_code", "bad_zip", "ZIP code must be 5 or 9 digits"))
            out["postal_code"] = None
        else:
            out["postal_code"] = m.group(1) + (f"-{m.group(2)}" if m.group(2) else "")
    if out["street1"] and re.search(r"\d{9}", out["street1"]):
        out["street1"] = None          # never store a 9-digit run (D3) — reported as missing above if it was all
        probs.append(problem(f"{path}.street1", "invalid", "street address is not valid"))
    return out, probs


def check_tax_id(t, path: str, contact_kind: str) -> tuple[dict, list[dict]]:
    """→ ({tax_id_kind, tax_id_last4, tax_id_salt, tax_id_digest} or all None, problems). The number never leaves."""
    empty = {"tax_id_kind": None, "tax_id_last4": None, "tax_id_salt": None, "tax_id_digest": None}
    if t is None:
        return empty, []
    if not isinstance(t, dict):
        return empty, [problem(path, "invalid", "tax_id must be an object {kind, value}")]
    kind = (t.get("kind") or "").strip().lower()
    digits = re.sub(r"[\s-]", "", str(t.get("value") or ""))
    probs = []
    if kind not in TAX_KINDS:
        probs.append(problem(f"{path}.kind", "invalid", "tax id kind must be ssn, itin or ein"))
    if not re.fullmatch(r"\d{9}", digits):
        probs.append(problem(f"{path}.value", "bad_tax_id_format", "a tax id is exactly 9 digits"))
        return empty, probs
    if contact_kind == "person" and kind == "ein":
        probs.append(problem(f"{path}.kind", "wrong_kind", "a person's tax id is an SSN or an ITIN"))
    if contact_kind == "entity" and kind in ("ssn", "itin"):
        probs.append(problem(f"{path}.kind", "wrong_kind", "a trust's tax id is an EIN"))
    if kind == "ssn":
        area, group, serial = digits[:3], digits[3:5], digits[5:]
        if area in ("000", "666") or area[0] == "9":
            probs.append(problem(f"{path}.value", "ssn_area", "not a valid SSN: the area number (first three digits) "
                                 "is never issued (000, 666 and 900–999; an ITIN starts with 9 — send it as an ITIN)"))
        if group == "00":
            probs.append(problem(f"{path}.value", "ssn_group", "not a valid SSN: the group number (digits 4–5) is 00"))
        if serial == "0000":
            probs.append(problem(f"{path}.value", "ssn_serial", "not a valid SSN: the serial number (last four) is 0000"))
    elif kind == "itin":
        if digits[0] != "9":
            probs.append(problem(f"{path}.value", "itin_prefix", "not a valid ITIN: an ITIN starts with 9"))
        if int(digits[3:5]) not in ITIN_GROUPS:
            probs.append(problem(f"{path}.value", "itin_group", "not a valid ITIN: digits 4–5 are outside the issued "
                                 "ranges (50–65, 70–88, 90–92, 94–99)"))
    elif kind == "ein":
        if digits[:2] in EIN_BAD_PREFIXES:
            probs.append(problem(f"{path}.value", "ein_prefix", "not a valid EIN: the prefix is not an assigned one"))
    salt = os.urandom(16)
    return {"tax_id_kind": kind if kind in TAX_KINDS else None, "tax_id_last4": digits[-4:], "tax_id_salt": salt,
            "tax_id_digest": hashlib.sha256(salt + digits.encode("ascii")).digest()}, probs


def age_on(dob: date, today: date) -> int:
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


def _date(v) -> date | None:
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10]) if v else None
    except ValueError:
        return None


def check_contact(body: dict, clock: date) -> tuple[dict, list[dict]]:
    """A contact's own fields → (the row to store, its problems). Role-dependent REQUIRED fields are checked on the
    draft (an owner needs an email; a minor does not)."""
    kind = (body.get("kind") or "person").strip().lower()
    probs = []
    if kind not in ("person", "entity"):
        probs.append(problem("kind", "invalid", "kind must be person or entity"))
        kind = "person"
    row = {"kind": kind, "legal_name": _txt(body.get("legal_name"))}
    if row["legal_name"] and re.search(r"\d{9}", row["legal_name"]):
        row["legal_name"] = None
        probs.append(problem("legal_name", "invalid", "legal name is not valid"))
    email = _txt(body.get("email"), 254)
    if email and (not EMAIL_RX.match(email) or re.search(r"\d{9}", email)):
        probs.append(problem("email", "bad_email", "email address is not valid"))
        email = None
    row["email"] = email.lower() if email else None
    phone = None
    if _txt(body.get("phone")):
        d = re.sub(r"\D", "", str(body.get("phone")))
        if len(d) == 11 and d[0] == "1":
            d = d[1:]
        if len(d) != 10:
            probs.append(problem("phone", "bad_phone", "phone must be a 10-digit US number"))
        else:
            phone = f"{d[:3]}-{d[3:6]}-{d[6:]}"
    row["phone"] = phone
    row["residential_address"], p = check_address(body.get("residential_address"), "residential_address",
                                                  residential=True)
    probs += p
    row["mailing_address"], p = check_address(body.get("mailing_address"), "mailing_address", residential=False)
    probs += p
    raw_dob = body.get("date_of_birth")
    dob = _date(raw_dob)
    row["age_years"] = row["age_as_of"] = None
    if raw_dob and dob is None:
        probs.append(problem("date_of_birth", "bad_date", "date of birth must be YYYY-MM-DD"))
    elif dob is not None:
        if dob >= clock:
            probs.append(problem("date_of_birth", "dob_future", "date of birth must be in the past"))
        else:
            row["age_years"], row["age_as_of"] = age_on(dob, clock), clock
    cit = (_txt(body.get("citizenship")) or "").lower() or None
    if cit and cit not in CITIZENSHIP:
        probs.append(problem("citizenship", "invalid", "citizenship is us_citizen, permanent_resident or visa"))
        cit = None
    row["citizenship"] = cit
    visa = re.sub(r"[\s-]", "", (_txt(body.get("visa_type")) or "")).upper() or None
    row["visa_type"] = visa if visa and re.fullmatch(r"[A-Z0-9]{1,6}", visa) else None
    row["visa_expiration"] = _date(body.get("visa_expiration"))
    if body.get("visa_expiration") and row["visa_expiration"] is None:
        probs.append(problem("visa_expiration", "bad_date", "visa expiration must be YYYY-MM-DD"))
    if cit == "visa" and visa and visa not in VISA_TYPES:
        probs.append(problem("visa_type", "visa_not_accepted", "this visa type is not accepted — accepted: "
                             + ", ".join(VISA_TYPES)))
    emp = body.get("employment")
    row["employment"] = None
    if emp is not None:
        if not isinstance(emp, dict):
            probs.append(problem("employment", "invalid", "employment must be an object"))
        else:
            st = (_txt(emp.get("status")) or "").lower() or None
            if st not in EMPLOYMENT:
                probs.append(problem("employment.status", "invalid", "employment status is one of "
                                     + ", ".join(EMPLOYMENT)))
            employer = _txt(emp.get("employer"), 120)
            if st == "employed" and not employer:
                probs.append(problem("employment.employer", "missing", "employer is required when employed"))
            row["employment"] = {"status": st if st in EMPLOYMENT else None, "employer": employer,
                                 "occupation": _txt(emp.get("occupation"), 120)}
            if re.search(r"\d{9}", json.dumps(row["employment"])):
                row["employment"] = {"status": row["employment"]["status"], "employer": None, "occupation": None}
    tax, p = check_tax_id(body.get("tax_id"), "tax_id", kind)
    probs += p
    row.update(tax)
    return row, probs


# ── households (the book's own table) ───────────────────────────────────────────────────────────────────────────────
async def household(conn, counterparty_id, name) -> tuple[int, dict]:
    name = _txt(name)
    if not name:
        raise RealtimeError(422, "household name is required", [problem("name", "missing", "name is required")])
    if re.search(r"\d{9}", name):
        raise RealtimeError(422, "household name is not valid", [problem("name", "invalid", "name is not valid")])
    row = await conn.fetchrow("SELECT external_id, name FROM cpsim.book_households WHERE counterparty_id = $1::uuid AND "
                              "valid_to IS NULL AND name = $2 ORDER BY external_id LIMIT 1", str(counterparty_id), name)
    if row:
        return 200, {"id": row["external_id"], "name": row["name"]}
    ext = new_id("rth-")
    await conn.execute("INSERT INTO cpsim.book_households (counterparty_id, external_id, name) VALUES ($1::uuid, $2, $3)",
                       str(counterparty_id), ext, name)
    return 201, {"id": ext, "name": name}


# ── contacts (VALID axis, restated by client_reference) ─────────────────────────────────────────────────────────────
CONTACT_COLS = ("kind", "legal_name", "email", "phone", "residential_address", "mailing_address", "citizenship",
                "visa_type", "visa_expiration", "employment", "age_years", "age_as_of", "tax_id_kind", "tax_id_last4",
                "tax_id_salt", "tax_id_digest")
_JSON_COLS = ("residential_address", "mailing_address", "employment")


def contact_out(r) -> dict:
    """What a response / the admin page may show: never a tax id beyond its last four, never a date of birth."""
    def j(v):
        return json.loads(v) if isinstance(v, str) else v
    return {"id": r["contact_id"], "client_reference": r["client_reference"], "kind": r["kind"],
            "legal_name": r["legal_name"], "email": r["email"], "phone": r["phone"],
            "residential_address": j(r["residential_address"]), "mailing_address": j(r["mailing_address"]),
            "citizenship": r["citizenship"], "visa_type": r["visa_type"],
            "visa_expiration": r["visa_expiration"].isoformat() if r["visa_expiration"] else None,
            "employment": j(r["employment"]), "age_years": r["age_years"],
            "tax_id_kind": r["tax_id_kind"], "tax_id_last4": r["tax_id_last4"],
            "problems": j(r["problems"]) or []}


async def upsert_contact(conn, counterparty_id, body, clock: date) -> tuple[int, dict]:
    if not isinstance(body, dict):
        raise RealtimeError(422, "the body must be a JSON object")
    ref = _txt(body.get("client_reference"), 80)
    if ref is not None and not REF_RX.match(ref):
        raise RealtimeError(422, "client_reference is not a valid identifier",
                            [problem("client_reference", "invalid", "client_reference is not a valid identifier")])
    row, probs = check_contact(body, clock)
    existing = await conn.fetchrow(
        "SELECT id, contact_id FROM cpsim.realtime_contacts WHERE counterparty_id = $1::uuid AND client_reference = $2 "
        "AND valid_to IS NULL FOR UPDATE", str(counterparty_id), ref) if ref else None
    if existing:
        contact_id, status = existing["contact_id"], 200
        await conn.execute("UPDATE cpsim.realtime_contacts SET valid_to = now() WHERE id = $1", existing["id"])
    else:
        contact_id, status = new_id("rtc-"), 201
    vals = [row[c] if c not in _JSON_COLS else (json.dumps(row[c]) if row[c] is not None else None)
            for c in CONTACT_COLS]
    cols = ", ".join(CONTACT_COLS)
    ph = ", ".join(f"${i + 5}" + ("::jsonb" if c in _JSON_COLS else "") for i, c in enumerate(CONTACT_COLS))
    r = await conn.fetchrow(
        f"INSERT INTO cpsim.realtime_contacts (counterparty_id, contact_id, client_reference, problems, {cols}) "
        f"VALUES ($1::uuid, $2, $3, $4::jsonb, {ph}) RETURNING *", str(counterparty_id), contact_id, ref,
        json.dumps(probs), *vals)
    return status, contact_out(r)


async def contacts(conn, counterparty_id, limit: int = 200) -> list[dict]:
    return [contact_out(r) for r in await conn.fetch(
        "SELECT * FROM cpsim.realtime_contacts WHERE counterparty_id = $1::uuid AND valid_to IS NULL ORDER BY valid_from "
        "DESC LIMIT $2", str(counterparty_id), limit)]


# ── drafts (VALID axis, restated by client_reference while still a draft) ───────────────────────────────────────────
def _json(v):
    return json.loads(v) if isinstance(v, str) else v


def draft_out(r, bens: list) -> dict:
    return {"id": r["draft_id"], "client_reference": r["client_reference"], "household_id": r["household_external_id"],
            "account_type": r["account_type"], "account_group": r["account_group"], "owners": _json(r["owners"]) or [],
            "beneficiaries": bens, "trusted_contact": _json(r["trusted_contact"]), "status": r["status"],
            "account_id": r["account_external_id"], "account_number": r["account_number"],
            "date_open": r["date_open"].isoformat() if r["date_open"] else None}


def _ben_out(b) -> dict:
    return {"tier": b["tier"], "name": b["name"], "kind": b["kind"], "contact_id": b["contact_id"],
            "relationship": b["relationship"], "percentage": format(b["percentage"], "f")
            if b["percentage"] is not None else None}


async def _bens(conn, row_ids: list) -> dict:
    out: dict = {}
    if not row_ids:
        return out
    for b in await conn.fetch("SELECT * FROM cpsim.realtime_beneficiaries WHERE draft_row_id = ANY($1::uuid[]) "
                              "ORDER BY draft_row_id, position", row_ids):
        out.setdefault(b["draft_row_id"], []).append(_ben_out(b))
    return out


def _clean_owner(o) -> dict | None:
    if not isinstance(o, dict):
        return None
    return {"contact_id": _safe(_txt(o.get("contact_id"), 40)), "role": _word(o.get("role"), 30)}


def _clean_trusted(t) -> dict | None:
    if t is None:
        return None
    if not isinstance(t, dict):
        return {"invalid": True}
    out = {"name": _safe(_txt(t.get("name"), 120)), "email": _safe((_txt(t.get("email"), 254) or "").lower() or None),
           "phone": None, "contact_id": _safe(_txt(t.get("contact_id"), 40)),
           "relationship": _safe(_txt(t.get("relationship"), 60))}
    d = re.sub(r"\D", "", str(t.get("phone") or ""))
    if len(d) == 11 and d[0] == "1":
        d = d[1:]
    out["phone"] = f"{d[:3]}-{d[3:6]}-{d[6:]}" if len(d) == 10 else (None if not d else "invalid")
    return out


async def upsert_draft(conn, counterparty_id, body) -> tuple[int, dict]:
    if not isinstance(body, dict):
        raise RealtimeError(422, "the body must be a JSON object")
    ref = _txt(body.get("client_reference"), 80)
    if ref is not None and not REF_RX.match(ref):
        raise RealtimeError(422, "client_reference is not a valid identifier")
    owners_in = body.get("owners")
    if owners_in is not None and not isinstance(owners_in, list):
        raise RealtimeError(422, "owners must be a list")
    bens_in = body.get("beneficiaries") or []
    if not isinstance(bens_in, list):
        raise RealtimeError(422, "beneficiaries must be a list")
    owners = [_clean_owner(o) or {"contact_id": None, "role": None} for o in (owners_in or [])]
    account_type = (_txt(body.get("account_type"), 60) or "").lower() or None
    if account_type and not re.fullmatch(r"[a-z0-9_]{1,60}", account_type):
        account_type = "invalid"
    existing = await conn.fetchrow(
        "SELECT * FROM cpsim.realtime_drafts WHERE counterparty_id = $1::uuid AND client_reference = $2 AND valid_to IS "
        "NULL FOR UPDATE", str(counterparty_id), ref) if ref else None
    if existing and existing["status"] == "opened":
        bens = await _bens(conn, [existing["id"]])
        return 200, draft_out(existing, bens.get(existing["id"], []))
    if existing:
        draft_id, status = existing["draft_id"], 200
        await conn.execute("UPDATE cpsim.realtime_drafts SET valid_to = now() WHERE id = $1", existing["id"])
    else:
        draft_id, status = new_id("rtd-"), 201
    group = _txt(body.get("account_group"), 120)
    if group and re.search(r"\d{9}", group):
        group = None
    trusted = _clean_trusted(body.get("trusted_contact"))
    r = await conn.fetchrow(
        "INSERT INTO cpsim.realtime_drafts (counterparty_id, draft_id, client_reference, household_external_id, "
        "account_type, account_group, owners, trusted_contact) VALUES ($1::uuid, $2, $3, $4, $5, $6, $7::jsonb, "
        "$8::jsonb) RETURNING *", str(counterparty_id), draft_id, ref, _txt(body.get("household_id"), 80),
        account_type, group, json.dumps(owners), json.dumps(trusted) if trusted is not None else None)
    for i, b in enumerate(bens_in):
        b = b if isinstance(b, dict) else {}
        pct = b.get("percentage")
        try:
            pct = Decimal(str(pct)) if pct is not None and str(pct).strip() != "" else None
        except InvalidOperation:
            pct = Decimal("-1")                                   # recorded as an invalid value → a problem
        name = _txt(b.get("name"), 120)
        await conn.execute(
            "INSERT INTO cpsim.realtime_beneficiaries (counterparty_id, draft_row_id, position, tier, name, kind, "
            "contact_id, relationship, percentage) VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, $8, $9)",
            str(counterparty_id), r["id"], i, _word(b.get("tier")),
            _safe(name), _word(b.get("kind")), _safe(_txt(b.get("contact_id"), 40)),
            _safe(_txt(b.get("relationship"), 60)), pct)
    bens = await _bens(conn, [r["id"]])
    return status, draft_out(r, bens.get(r["id"], []))


async def _live_draft(conn, counterparty_id, draft_id: str, *, lock: bool = False):
    return await conn.fetchrow(
        "SELECT * FROM cpsim.realtime_drafts WHERE counterparty_id = $1::uuid AND draft_id = $2 AND valid_to IS NULL"
        + (" FOR UPDATE" if lock else ""), str(counterparty_id), draft_id)


async def get_draft(conn, counterparty_id, draft_id: str) -> dict:
    r = await _live_draft(conn, counterparty_id, draft_id)
    if r is None:
        raise RealtimeError(404, "no such account draft")
    bens = await _bens(conn, [r["id"]])
    return draft_out(r, bens.get(r["id"], []))


# ── validation (D2): EVERY problem, field by field ──────────────────────────────────────────────────────────────────
def _age_now(c, clock: date) -> int | None:
    if c["age_years"] is None:
        return None
    return int(c["age_years"]) + max((clock - c["age_as_of"]).days, 0) // 365      # simplification, documented


async def validate(conn, counterparty_id, d, clock: date) -> list[dict]:
    """``d`` = the live draft row. Reads every contact the draft names in ONE query."""
    probs: list[dict] = []
    owners = _json(d["owners"]) or []
    trusted = _json(d["trusted_contact"])
    bens = (await _bens(conn, [d["id"]])).get(d["id"], [])
    # household
    if not d["household_external_id"]:
        probs.append(problem("household_id", "missing", "household is required (create or find it first)"))
    elif not await conn.fetchval("SELECT 1 FROM cpsim.book_households WHERE counterparty_id = $1::uuid AND external_id "
                                 "= $2 AND valid_to IS NULL", str(counterparty_id), d["household_external_id"]):
        probs.append(problem("household_id", "unknown", "no such household at the custodian"))
    # account type (V2)
    t = d["account_type"]
    shape = None
    if not t:
        probs.append(problem("account_type", "missing", "account type is required"))
    elif t in UNSUPPORTED_TYPES:
        probs.append(problem("account_type", "unsupported_type", f"{UNSUPPORTED_TYPES[t]}: {UNSUPPORTED_MESSAGE}"))
    elif t not in ACCOUNT_TYPES:
        probs.append(problem("account_type", "unknown_type", "unknown account type"))
    else:
        shape = ACCOUNT_TYPES[t][1]
    # the contacts, in one read
    ids = sorted({o.get("contact_id") for o in owners if o.get("contact_id")}
                 | {b["contact_id"] for b in bens if b.get("contact_id")}
                 | ({trusted.get("contact_id")} if isinstance(trusted, dict) and trusted.get("contact_id") else set()))
    cs = {r["contact_id"]: r for r in await conn.fetch(
        "SELECT * FROM cpsim.realtime_contacts WHERE counterparty_id = $1::uuid AND contact_id = ANY($2::text[]) AND "
        "valid_to IS NULL", str(counterparty_id), ids)} if ids else {}
    # owners and roles (V3)
    if not owners:
        probs.append(problem("owners", "missing", "at least one owner is required"))
    counts: dict[str, int] = {}
    for o in owners:
        if o.get("role"):
            counts[o["role"]] = counts.get(o["role"], 0) + 1
    if shape:
        rules = SHAPE_ROLES[shape]
        label = ACCOUNT_TYPES[t][0]
        for role, (lo, hi) in rules.items():
            n = counts.get(role, 0)
            if n < lo or (hi is not None and n > hi):
                want = f"exactly {lo}" if hi == lo else f"at least {lo}"
                probs.append(problem("owners", f"role_count_{role}",
                                     f"{label} needs {want} {role.replace('_', ' ')} (has {n})"))
        for i, o in enumerate(owners):
            if o.get("role") and o["role"] not in rules:
                probs.append(problem(f"owners[{i}].role", "role_not_used",
                                     f"{o['role'].replace('_', ' ')} is not a role of a {label} account"))
    emails_seen: dict[str, int] = {}
    owner_ids = set()
    for i, o in enumerate(owners):
        p = f"owners[{i}]"
        cid, role = o.get("contact_id"), o.get("role")
        if not role or role not in PERSON_ROLES + ENTITY_ROLES:
            probs.append(problem(f"{p}.role", "invalid", "role is one of " + ", ".join(PERSON_ROLES + ENTITY_ROLES)))
        if not cid:
            probs.append(problem(f"{p}.contact_id", "missing", "the owner's contact is required"))
            continue
        c = cs.get(cid)
        if c is None:
            probs.append(problem(f"{p}.contact_id", "unknown", "no such contact at the custodian"))
            continue
        owner_ids.add(cid)
        for q in _json(c["problems"]) or []:
            probs.append({**q, "field": f"{p}.{q['field']}"})
        entity_role = role in ENTITY_ROLES
        if entity_role and c["kind"] != "entity":
            probs.append(problem(f"{p}.kind", "wrong_kind", "the trust must be an entity contact"))
        if role in PERSON_ROLES and c["kind"] != "person":
            probs.append(problem(f"{p}.kind", "wrong_kind", "this role is held by a person"))
        need = ["legal_name", "residential_address", "tax_id"]
        if not entity_role:
            need += ["date_of_birth", "citizenship"]
            if role != "minor":
                need += ["email", "phone", "employment"]
        have = {"legal_name": c["legal_name"], "residential_address": c["residential_address"],
                "tax_id": c["tax_id_last4"], "date_of_birth": c["age_years"], "citizenship": c["citizenship"],
                "email": c["email"], "phone": c["phone"], "employment": c["employment"]}
        for f in need:
            if have[f] is None:
                probs.append(problem(f"{p}.{f}", "missing", f"{f.replace('_', ' ')} is required"))
        age = _age_now(c, clock)
        if age is not None and not entity_role:
            if role == "minor" and age >= MINOR_AGE:
                probs.append(problem(f"{p}.date_of_birth", "not_a_minor",
                                     f"the minor must be under {MINOR_AGE} on the application date"))
            if role != "minor" and age < MINOR_AGE:
                probs.append(problem(f"{p}.date_of_birth", "not_an_adult",
                                     f"this role needs an adult ({MINOR_AGE} or over)"))
        if c["citizenship"] == "visa" and not entity_role:
            if not c["visa_type"]:
                probs.append(problem(f"{p}.visa_type", "missing", "visa type is required for a visa holder"))
            if c["visa_expiration"] is None:
                probs.append(problem(f"{p}.visa_expiration", "missing", "visa expiration is required for a visa "
                                                                         "holder"))
            elif c["visa_type"] in VISA_TYPES and c["visa_expiration"] < clock + timedelta(days=VISA_MIN_DAYS):
                probs.append(problem(f"{p}.visa_expiration", "visa_expiring",
                                     f"the visa must not expire within {VISA_MIN_DAYS} days of the application date"))
        if c["email"]:
            if c["email"] in emails_seen:
                probs.append(problem(f"{p}.email", "duplicate_email", "two owners of this account share an email — "
                                                                      "every client needs a unique email"))
            emails_seen[c["email"]] = i
    # unique email across the custodian's contacts (V12), one query
    if emails_seen:
        taken = {r["e"] for r in await conn.fetch(
            "SELECT DISTINCT lower(email) AS e FROM cpsim.realtime_contacts WHERE counterparty_id = $1::uuid AND "
            "valid_to IS NULL AND lower(email) = ANY($2::text[]) AND NOT (contact_id = ANY($3::text[]))",
            str(counterparty_id), list(emails_seen), sorted(owner_ids))}
        for e, i in emails_seen.items():
            if e in taken:
                probs.append(problem(f"owners[{i}].email", "duplicate_email",
                                     "this email is already used by another client — every client needs a unique email"))
    # beneficiaries (V13)
    totals: dict[str, Decimal] = {}
    for i, b in enumerate(bens):
        p = f"beneficiaries[{i}]"
        if b["tier"] not in TIERS:
            probs.append(problem(f"{p}.tier", "invalid", "tier is primary or contingent"))
        if not b["name"] and not b["contact_id"]:
            probs.append(problem(f"{p}.name", "missing", "each beneficiary is a named person or entity"))
        if b["kind"] not in ("person", "entity"):
            probs.append(problem(f"{p}.kind", "invalid", "kind is person or entity"))
        if b["contact_id"] and b["contact_id"] not in cs:
            probs.append(problem(f"{p}.contact_id", "unknown", "no such contact at the custodian"))
        pct = Decimal(b["percentage"]) if b["percentage"] is not None else None
        if pct is None:
            probs.append(problem(f"{p}.percentage", "missing", "percentage is required"))
        elif pct <= 0 or pct > 100:
            probs.append(problem(f"{p}.percentage", "invalid", "percentage must be more than 0 and at most 100"))
        elif b["tier"] in TIERS:
            totals[b["tier"]] = totals.get(b["tier"], Decimal("0")) + pct
    for tier, total in totals.items():
        if total != Decimal("100"):
            probs.append(problem(f"beneficiaries.{tier}", "percent_total",
                                 f"{tier} beneficiaries must total exactly 100% (they total {format(total, 'f')}%)"))
    # trusted contact (V14)
    if isinstance(trusted, dict):
        if trusted.get("invalid"):
            probs.append(problem("trusted_contact", "invalid", "trusted contact must be an object"))
        else:
            if not trusted.get("name"):
                probs.append(problem("trusted_contact.name", "missing", "trusted contact needs a name"))
            if trusted.get("phone") == "invalid":
                probs.append(problem("trusted_contact.phone", "bad_phone", "phone must be a 10-digit US number"))
            if not trusted.get("email") and trusted.get("phone") in (None, "invalid"):
                probs.append(problem("trusted_contact.email", "missing", "trusted contact needs an email or a phone"))
            if trusted.get("email") and not EMAIL_RX.match(trusted["email"]):
                probs.append(problem("trusted_contact.email", "bad_email", "email address is not valid"))
            owner_names = {(cs[c]["legal_name"] or "").strip().lower() for c in owner_ids if cs[c]["legal_name"]}
            owner_emails = {cs[c]["email"] for c in owner_ids if cs[c]["email"]}
            if (trusted.get("contact_id") and trusted["contact_id"] in owner_ids) \
                    or (trusted.get("email") and trusted["email"] in owner_emails) \
                    or (trusted.get("name") and trusted["name"].strip().lower() in owner_names):
                probs.append(problem("trusted_contact", "is_owner", "the trusted contact cannot be an owner of the "
                                                                    "account"))
    return probs


async def _log(conn, counterparty_id, draft_id: str, action: str, outcome: str, probs: list, clock: date,
               account: str | None = None) -> None:
    await conn.execute(
        "INSERT INTO cpsim.realtime_validation_log (counterparty_id, draft_id, action, outcome, problems, problem_count, "
        "clock_date, account_external_id) VALUES ($1::uuid, $2, $3, $4, $5::jsonb, $6, $7, $8)",
        str(counterparty_id), draft_id, action, outcome, json.dumps(probs), len(probs), clock, account)


async def check(conn, counterparty_id, draft_id: str, clock: date) -> dict:
    """``…/validate``: the problems; opens nothing."""
    d = await _live_draft(conn, counterparty_id, draft_id)
    if d is None:
        raise RealtimeError(404, "no such account draft")
    if d["status"] == "opened":
        await _log(conn, counterparty_id, draft_id, "validate", "already_open", [], clock, d["account_external_id"])
        return {"ok": True, "problems": [], "status": "opened"}
    probs = await validate(conn, counterparty_id, d, clock)
    await _log(conn, counterparty_id, draft_id, "validate", "refused" if probs else "ok", probs, clock)
    return {"ok": not probs, "problems": probs, "status": d["status"]}


def account_out(d) -> dict:
    t = d["account_type"]
    return {"id": d["account_external_id"], "account_number": d["account_number"],
            "account_type": ACCOUNT_TYPES[t][2] if t in ACCOUNT_TYPES else None, "status": "ACTIVE",
            "date_open": d["date_open"].strftime("%Y/%m/%d"), "household_id": d["household_external_id"]}


async def submit(conn, counterparty_id, draft_id: str, clock: date) -> tuple[int, dict]:
    """``…/submit``: in good order → OPENED at once (201); a repeat → the same account (200); else 422 + problems."""
    d = await _live_draft(conn, counterparty_id, draft_id, lock=True)
    if d is None:
        raise RealtimeError(404, "no such account draft")
    bens = await _bens(conn, [d["id"]])
    if d["status"] == "opened":
        await _log(conn, counterparty_id, draft_id, "submit", "already_open", [], clock, d["account_external_id"])
        return 200, {"draft": draft_out(d, bens.get(d["id"], [])), "account": account_out(d)}
    probs = await validate(conn, counterparty_id, d, clock)
    if probs:
        await _log(conn, counterparty_id, draft_id, "submit", "refused", probs, clock)
        # returned, not raised: the refusal's validation-log row commits with the request (nothing else was written)
        return 422, {"detail": "the account draft is not in good order", "problems": probs}
    ext = new_id("rta-")
    while True:
        number = "CPSIM" + "".join(secrets.choice("0123456789") for _ in range(9))
        if not await conn.fetchval("SELECT 1 FROM cpsim.book_accounts WHERE counterparty_id = $1::uuid AND "
                                   "account_number = $2", str(counterparty_id), number):
            break
    label, _, feed_type = ACCOUNT_TYPES[d["account_type"]]
    owners = _json(d["owners"]) or []
    first = next((o["contact_id"] for o in owners if o.get("role") in ("owner", "trust", "minor")), None)
    name_of = await conn.fetchval("SELECT legal_name FROM cpsim.realtime_contacts WHERE counterparty_id = $1::uuid AND "
                                  "contact_id = $2 AND valid_to IS NULL", str(counterparty_id), first) if first else None
    acct_name = f"{name_of or 'Client'} — {label}"[:200]
    await conn.execute(
        "INSERT INTO cpsim.book_accounts (counterparty_id, external_id, household_external_id, name, account_type, "
        "date_open, account_number) VALUES ($1::uuid, $2, $3, $4, $5, $6, $7)", str(counterparty_id), ext,
        d["household_external_id"], acct_name, feed_type, clock, number)
    await conn.execute("UPDATE cpsim.realtime_drafts SET valid_to = now() WHERE id = $1", d["id"])
    o = await conn.fetchrow(
        "INSERT INTO cpsim.realtime_drafts (counterparty_id, draft_id, client_reference, household_external_id, "
        "account_type, account_group, owners, trusted_contact, status, account_external_id, account_number, date_open, "
        "opened_at) SELECT counterparty_id, draft_id, client_reference, household_external_id, account_type, "
        "account_group, owners, trusted_contact, 'opened', $2, $3, $4, $5 FROM cpsim.realtime_drafts WHERE id = $1 "
        "RETURNING *", d["id"], ext, number, clock, O.now())
    await conn.execute(
        "INSERT INTO cpsim.realtime_beneficiaries (counterparty_id, draft_row_id, position, tier, name, kind, contact_id, "
        "relationship, percentage) SELECT counterparty_id, $2, position, tier, name, kind, contact_id, relationship, "
        "percentage FROM cpsim.realtime_beneficiaries WHERE draft_row_id = $1", d["id"], o["id"])
    await _log(conn, counterparty_id, draft_id, "submit", "opened", [], clock, ext)
    nb = await _bens(conn, [o["id"]])
    return 201, {"draft": draft_out(o, nb.get(o["id"], [])), "account": account_out(o)}


# ── admin reads (the Realtime tab) ──────────────────────────────────────────────────────────────────────────────────
async def drafts(conn, counterparty_id, limit: int = 100) -> list[dict]:
    rows = await conn.fetch("SELECT * FROM cpsim.realtime_drafts WHERE counterparty_id = $1::uuid AND valid_to IS NULL "
                            "ORDER BY valid_from DESC LIMIT $2", str(counterparty_id), limit)
    bens = await _bens(conn, [r["id"] for r in rows])
    return [draft_out(r, bens.get(r["id"], [])) for r in rows]


async def validation_log(conn, counterparty_id, limit: int = 100) -> list[dict]:
    return [{"id": r["id"], "draft_id": r["draft_id"], "action": r["action"], "outcome": r["outcome"],
             "problems": _json(r["problems"]) or [], "problem_count": r["problem_count"],
             "clock_date": r["clock_date"].isoformat(), "account_id": r["account_external_id"],
             "created_at": r["created_at"].isoformat()}
            for r in await conn.fetch(
                "SELECT id::text, draft_id, action, outcome, problems, problem_count, clock_date, account_external_id, "
                "created_at FROM cpsim.realtime_validation_log WHERE counterparty_id = $1::uuid ORDER BY created_at DESC "
                "LIMIT $2", str(counterparty_id), limit)]


def vocabularies() -> dict:
    """The custodian's own account-type lists (published YES / NO) — the client and the admin page read them here."""
    return {"account_types": [{"key": k, "label": v[0], "shape": v[1]} for k, v in ACCOUNT_TYPES.items()],
            "unsupported_account_types": [{"key": k, "label": v, "reason": UNSUPPORTED_MESSAGE}
                                          for k, v in UNSUPPORTED_TYPES.items()],
            "roles": list(PERSON_ROLES + ENTITY_ROLES), "visa_types": list(VISA_TYPES),
            "citizenship": list(CITIZENSHIP), "employment_statuses": list(EMPLOYMENT), "tiers": list(TIERS),
            "minor_age": MINOR_AGE, "visa_min_days": VISA_MIN_DAYS}
