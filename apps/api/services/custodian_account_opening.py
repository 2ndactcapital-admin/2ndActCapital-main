"""cpsim06 D4 / D5 — "Open account at custodian" on a CRM household (SIMULATED custodian only).

What it does, and what it reuses (no second mechanism):
  * PRE-FILL (:func:`context`): the household's members from the CRM — names, email, phone, residential / mailing
    addresses (``entity_addresses``), the current employer (``entity_employment``), trustee / parent / beneficiary
    relationships (``entity_relationships``), tax ids as LAST FOUR ONLY (``entity_tax_ids`` / ``entities.tax_id``) and
    whether a date of birth is on record (never the date). ONE query per table for all members — no N+1. The account
    types are the custodian's published YES list (with each one's registration type mapped through the org's own
    vocabulary) and its NO list (offered disabled, with the reason);
  * CHECK (:func:`check`): builds the request server-side (the tax id and date of birth are read from the CRM HERE,
    at send time, or taken from what the opener typed for this one request — CPSIM06_NOTES §1d), sends contacts + the
    draft and asks the counterparty to VALIDATE — nothing is opened, nothing is written in Hollisworks;
  * OPEN (:func:`open_account`): the same, then SUBMIT. Opened → the account is recorded through the EXISTING
    ``household_admin.add_account`` (custodian ``ALTRUIST``, the returned number stored masked + keyed hash as always,
    the primary owner, registration type / tax status mapped through the org vocabulary, REP7B's default authority /
    custody), with the crosswalk (``portfolio.external_references`` source ``ALTRUIST_SIMULATOR``, record type
    ``account``, the counterparty's account id) written in the SAME transaction (``on_created``), then an immediate
    sync of that account (``sync_resolved_accounts(only_account_ids=…)``). Refused → every problem, field by field;
    nothing is created in Hollisworks (an audit row records the refusal's problem codes — never a value);
  * idempotent: the counterparty answers a repeat submit with the same account, and an account id already crosswalked
    returns the existing Hollisworks account — never a second add_account.

D5 guard: the org has ``counterparty_sim.allowed`` and a configured counterparty, and (a non-test org) the household
passes ``counterparty_sim_may_connect``. Sandbox / production: refused before any request — :data:`NO_REALTIME`.

Sensitive data: a tax id or date of birth is never returned to the page, written to a log or an audit row; only last
four. Every network call goes through ``services.altruist_realtime_sim`` (transport threaded on every call; the
database connection is RELEASED before any HTTP call).
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import date
from typing import Any

from services import altruist_realtime_sim as RS
from services import authority as AUTH
from services import household_admin as HA
from services.altruist_oauth import SIMULATOR, AltruistOAuthError, AltruistSimulatorRefused, simulator_counterparty
from services.crm_config import account_vocab

CUSTODIAN_CODE = "ALTRUIST"          # simulator-fed accounts carry it (CPSIM06_NOTES §1c); the crosswalk marks simulated
XWALK_SOURCE = "ALTRUIST_SIMULATOR"
RECORD_TYPE = "account"
NO_REALTIME = RS.NO_REALTIME
NOT_DEMO = ("this household is not a demo household — a simulated custodian opens accounts only for a test organisation "
            "or for a demo household")
LABEL = "SIMULATED"

#: the custodian's PUBLISHED YES list (keys ASSUMED; the simulator's cpsim/realtime.ACCOUNT_TYPES carries the same —
#: the conformance group proves the two agree): key → (label, shape, (registration key, tax status key))
ACCOUNT_TYPES = {
    "individual": ("Individual", "single", ("individual", "taxable")),
    "individual_cash": ("Individual Cash", "single", ("individual", "taxable")),
    "jtwros": ("Joint Tenants With Right of Survivorship", "joint", ("joint", "taxable")),
    "trust": ("Trust (revocable or irrevocable)", "trust", ("trust", "taxable")),
    "trust_cash": ("Trust Cash", "trust", ("trust", "taxable")),
    "ira": ("IRA (Traditional)", "single", ("ira", "tax_deferred")),
    "roth_ira": ("Roth IRA", "single", ("roth_ira", "tax_exempt")),
    "rollover_ira": ("Rollover IRA", "single", ("401k_rollover", "tax_deferred")),
    "beneficiary_ira": ("Beneficiary IRA", "single", ("ira", "tax_deferred")),
    "simple_ira": ("SIMPLE IRA", "single", ("ira", "tax_deferred")),
    "solo_401k": ("Solo 401(k)", "single", ("other", "tax_deferred")),
    "utma": ("UTMA", "custodial", ("other", "taxable")),
    "ugma": ("UGMA", "custodial", ("other", "taxable")),
    "minor_roth_ira": ("Minor Roth IRA", "custodial", ("roth_ira", "tax_exempt")),
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
UNSUPPORTED_REASON = ("not supported by the custodian's API for account opening — open it through the custodian's own "
                      "process")
SHAPE_ROLES = {"single": ["owner"], "joint": ["owner", "joint_owner"], "trust": ["trust", "trustee"],
               "custodial": ["minor", "custodian"]}
#: the role whose CRM record becomes the Hollisworks account's primary owner
PRIMARY_ROLE = {"single": "owner", "joint": "owner", "trust": "trust", "custodial": "minor"}
ROLE_LABELS = {"owner": "Owner", "joint_owner": "Joint owner", "trust": "Trust", "trustee": "Trustee",
               "minor": "Minor", "custodian": "Custodian (adult)"}
CITIZENSHIP = (("us_citizen", "US citizen"), ("permanent_resident", "Permanent resident"), ("visa", "Visa holder"))
EMPLOYMENT = (("employed", "Employed"), ("self_employed", "Self-employed"), ("retired", "Retired"),
              ("unemployed", "Not employed"), ("student", "Student"))
VISA_TYPES = ("E1", "E2", "E3", "F1", "G4", "H1B", "L1", "O1", "TN1")
OVERRIDABLE = ("legal_name", "email", "phone", "residential_address", "mailing_address", "citizenship", "visa_type",
               "visa_expiration", "employment", "date_of_birth", "tax_id")
MINOR_AGE = 18


class OpeningRefused(Exception):
    """Not available / not allowed here (→ 409 with the reason; nothing sent)."""


def _u(v) -> str:
    return str(uuid.UUID(str(v)))


def _digits(v) -> str:
    return re.sub(r"\D", "", str(v or ""))


def last4(v) -> str | None:
    d = _digits(v)
    return d[-4:] if len(d) >= 4 else None


def mask(l4: str | None) -> str:
    return f"•••• {l4}" if l4 else "••••"


# ── D5: availability ────────────────────────────────────────────────────────────────────────────────────────────────
async def availability(conn, org_id, household_id, environment: str = SIMULATOR) -> dict:
    """``{available, reason, environment, counterparty}`` — the action exists only for the simulated custodian."""
    if environment != SIMULATOR:
        return {"available": False, "reason": NO_REALTIME, "environment": environment, "counterparty": None}
    try:
        slug = await simulator_counterparty(conn, org_id=_u(org_id))
    except AltruistSimulatorRefused as exc:
        return {"available": False, "reason": f"{NO_REALTIME} — {exc}", "environment": environment,
                "counterparty": None}
    ok = await conn.fetchval("SELECT public.counterparty_sim_may_connect($1::uuid, $2::uuid)", _u(org_id),
                             _u(household_id))
    if not ok:
        return {"available": False, "reason": NOT_DEMO, "environment": environment, "counterparty": slug}
    return {"available": True, "reason": None, "environment": environment, "counterparty": slug}


# ── CRM reads (one query per table for the whole household) ─────────────────────────────────────────────────────────
async def _crm(conn, org_id, member_ids: list[str]) -> dict:
    """Everything the pre-fill and the request need, keyed by entity id. Server-side only: the full tax id and the
    date of birth stay in this dict and are never returned by :func:`context`."""
    ids = [_u(m) for m in member_ids]
    ents = {str(r["id"]): dict(r) for r in await conn.fetch(
        "SELECT id, entity_type::text AS entity_type, display_name, first_name, middle_name, surname, legal_name, "
        "tax_id, date_of_birth, date_of_death, primary_email, primary_phone, citizenship_status FROM entities "
        "WHERE org_id = $1::uuid AND id = ANY($2::uuid[]) AND valid_to IS NULL AND system_to IS NULL",
        _u(org_id), ids)}
    addrs: dict[str, list] = {}
    for r in await conn.fetch(
            "SELECT entity_id, address_type::text AS address_type, street1, street2, city, state, postal_code, country, "
            "is_primary FROM entity_addresses WHERE org_id = $1::uuid AND entity_id = ANY($2::uuid[]) AND valid_to IS "
            "NULL AND system_to IS NULL ORDER BY is_primary DESC, created_at", _u(org_id), ids):
        addrs.setdefault(str(r["entity_id"]), []).append(dict(r))
    taxes: dict[str, list] = {}
    for r in await conn.fetch(
            "SELECT entity_id, tax_id_type::text AS kind, tax_id_last4, tax_id_encrypted FROM entity_tax_ids WHERE "
            "org_id = $1::uuid AND entity_id = ANY($2::uuid[]) AND valid_to IS NULL AND system_to IS NULL "
            "ORDER BY is_primary DESC", _u(org_id), ids):
        taxes.setdefault(str(r["entity_id"]), []).append(dict(r))
    jobs: dict[str, dict] = {}
    for r in await conn.fetch(
            "SELECT j.employee_id, j.title, e.display_name AS employer FROM entity_employment j JOIN entities e ON "
            "e.id = j.employer_id WHERE j.org_id = $1::uuid AND j.employee_id = ANY($2::uuid[]) AND j.is_current AND "
            "j.valid_to IS NULL AND j.system_to IS NULL ORDER BY j.start_date DESC NULLS LAST", _u(org_id), ids):
        jobs.setdefault(str(r["employee_id"]), dict(r))
    rels = [dict(r) for r in await conn.fetch(
        "SELECT from_entity_id::text AS f, to_entity_id::text AS t, relationship_type AS k FROM entity_relationships "
        "WHERE org_id = $1::uuid AND (from_entity_id = ANY($2::uuid[]) OR to_entity_id = ANY($2::uuid[])) AND "
        "relationship_type = ANY($3::text[]) AND valid_to IS NULL AND system_to IS NULL", _u(org_id), ids,
        ["trustee", "co_trustee", "successor_trustee", "beneficiary", "parent_of", "spouse"])]
    names = {}
    other = sorted({r["f"] for r in rels} | {r["t"] for r in rels})
    if other:
        names = {str(r["id"]): r["display_name"] for r in await conn.fetch(
            "SELECT id, display_name FROM entities WHERE org_id = $1::uuid AND id = ANY($2::uuid[]) AND valid_to IS NULL "
            "AND system_to IS NULL", _u(org_id), other)}
    return {"entities": ents, "addresses": addrs, "tax_ids": taxes, "jobs": jobs, "relationships": rels,
            "names": names}


def _address(rows: list, kind: str) -> dict | None:
    for r in rows or []:
        if r["address_type"] == kind:
            return {k: r[k] for k in ("street1", "street2", "city", "state", "postal_code", "country")}
    return None


def _kind(entity_type: str) -> str:
    return "person" if entity_type == "individual" else "entity"


def _citizenship(v) -> str | None:
    v = (v or "").strip().lower().replace(" ", "_").replace("-", "_")
    return v if v in ("us_citizen", "permanent_resident", "visa") else None


def _infer_kind(contact_kind: str, digits: str) -> str:
    """The kind of a number with no recorded type: a legal entity's is an EIN; a person's starting with 9 is an ITIN
    (no SSN is issued in the 900 area), otherwise an SSN."""
    if contact_kind == "entity":
        return "ein"
    return "itin" if digits[:1] == "9" else "ssn"


def _tax_on_record(e: dict, taxes: list) -> dict:
    """What the page may know about a tax id: whether the FULL number can be read at send time ('full' — the legacy
    entities.tax_id), only a hash ('hash_only' — the opener types it), or none; and its last four."""
    if _digits(e.get("tax_id")) and len(_digits(e.get("tax_id"))) == 9:
        k = (taxes[0]["kind"] if taxes else None) or _infer_kind(_kind(e["entity_type"]), _digits(e["tax_id"]))
        return {"on_record": "full", "last4": last4(e["tax_id"]), "kind": k if k in ("ssn", "itin", "ein") else None}
    if taxes:
        k = taxes[0]["kind"]
        return {"on_record": "hash_only", "last4": taxes[0]["tax_id_last4"],
                "kind": k if k in ("ssn", "itin", "ein") else None}
    return {"on_record": "none", "last4": None, "kind": None}


def _is_minor(e: dict, today: date) -> bool | None:
    dob = e.get("date_of_birth")
    if not dob:
        return None
    age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
    return age < MINOR_AGE


def _member_prefill(eid: str, crm: dict, today: date) -> dict:
    e = crm["entities"][eid]
    a = crm["addresses"].get(eid) or []
    job = crm["jobs"].get(eid)
    return {"entity_id": eid, "display_name": e["display_name"], "entity_type": e["entity_type"],
            "kind": _kind(e["entity_type"]), "legal_name": e.get("legal_name") or e["display_name"],
            "email": e.get("primary_email"), "phone": e.get("primary_phone"),
            "residential_address": _address(a, "primary_residence") or (_address(a, "registered")
                                                                        if _kind(e["entity_type"]) == "entity" else None),
            "mailing_address": _address(a, "mailing"),
            "citizenship": _citizenship(e.get("citizenship_status")),
            "employment": {"status": "employed", "employer": job["employer"], "occupation": job.get("title")}
            if job else None,
            "date_of_birth_on_record": bool(e.get("date_of_birth")), "is_minor": _is_minor(e, today),
            "deceased": bool(e.get("date_of_death")),
            "tax_id": _tax_on_record(e, crm["tax_ids"].get(eid) or [])}


def _suggestions(crm: dict, member_ids: list[str]) -> dict:
    """Owners / roles / beneficiaries the CRM implies (the opener confirms them)."""
    members = set(member_ids)
    ents = crm["entities"]
    trusts = [m for m in member_ids if ents[m]["entity_type"] == "trust"]
    out = {"trustees": {}, "parents": {}, "beneficiaries": {}}
    for r in crm["relationships"]:
        if r["k"] in ("trustee", "co_trustee", "successor_trustee") and r["t"] in trusts and r["f"] in members:
            out["trustees"].setdefault(r["t"], []).append(r["f"])
        elif r["k"] == "parent_of" and r["t"] in members and r["f"] in members:
            out["parents"].setdefault(r["t"], []).append(r["f"])
        elif r["k"] == "beneficiary" and r["t"] in members:
            out["beneficiaries"].setdefault(r["t"], []).append(
                {"entity_id": r["f"], "name": crm["names"].get(r["f"]), "tier": "primary"})
    return out


async def _members(conn, org_id, household_id) -> tuple[dict, list[str]]:
    hh = await HA._household(conn, org_id, household_id)
    ids = [str(r["entity_id"]) for r in await conn.fetch(
        "SELECT m.entity_id FROM household_memberships m JOIN entities e ON e.id = m.entity_id WHERE m.household_id = "
        "$1 AND e.org_id = $2::uuid AND e.valid_to IS NULL AND e.system_to IS NULL AND e.entity_type::text <> ALL($3)",
        hh["id"], _u(org_id), list(HA.REFUSED_ENTITY_TYPES))]
    return hh, ids


def account_type_options(vocab: dict) -> dict:
    """The custodian's YES list (each with its Hollisworks registration / tax status from the org vocabulary) and its
    NO list (disabled, with the reason) — served in the envelope, never hardcoded on the page (Rule 1)."""
    reg_keys = {o["key"]: o["label"] for o in vocab.get("registration_types") or []}
    tax_keys = {o["key"]: o["label"] for o in vocab.get("tax_statuses") or []}
    yes = []
    for k, (label, shape, (reg, tax)) in ACCOUNT_TYPES.items():
        r = reg if reg in reg_keys else ("other" if "other" in reg_keys else None)
        yes.append({"key": k, "label": label, "shape": shape, "roles": SHAPE_ROLES[shape], "supported": True,
                    "registration_type": r, "registration_label": reg_keys.get(r),
                    "tax_status": tax if tax in tax_keys else None, "tax_status_label": tax_keys.get(tax)})
    no = [{"key": k, "label": v, "supported": False, "reason": UNSUPPORTED_REASON} for k, v in UNSUPPORTED_TYPES.items()]
    return {"supported": yes, "unsupported": no}


def vocabularies(vocab: dict) -> dict:
    return {"account_types": account_type_options(vocab),
            "roles": [{"key": k, "label": v} for k, v in ROLE_LABELS.items()],
            "citizenship": [{"key": k, "label": v} for k, v in CITIZENSHIP],
            "employment_statuses": [{"key": k, "label": v} for k, v in EMPLOYMENT],
            "visa_types": list(VISA_TYPES), "tiers": [{"key": "primary", "label": "Primary"},
                                                     {"key": "contingent", "label": "Contingent"}],
            "minor_age": MINOR_AGE}


async def context(pool, *, org_id, household_id, environment: str = SIMULATOR, today: date | None = None) -> dict:
    """The form's envelope: availability, the members pre-filled from the CRM (last four only, no date of birth),
    suggestions and the vocabularies."""
    today = today or date.today()
    async with pool.acquire() as conn:
        hh, member_ids = await _members(conn, org_id, household_id)
        avail = await availability(conn, org_id, hh["id"], environment)
        vocab = await account_vocab(conn, org_id)
        crm = await _crm(conn, org_id, member_ids) if member_ids else {"entities": {}, "addresses": {}, "tax_ids": {},
                                                                         "jobs": {}, "relationships": [], "names": {}}
    members = [_member_prefill(m, crm, today) for m in member_ids if m in crm["entities"]]
    return {"household": {"id": str(hh["id"]), "name": hh["name"]}, "availability": avail, "label": LABEL,
            "members": members, "suggestions": _suggestions(crm, [m["entity_id"] for m in members]),
            "vocabularies": vocabularies(vocab)}


# ── building the request (server-side; the only place a tax id / DOB is assembled) ──────────────────────────────────
def _crm_hash_candidates(value: str) -> set[str]:
    d = _digits(value)
    forms = {value, value.strip(), d}
    if len(d) == 9:
        forms |= {f"{d[:3]}-{d[3:5]}-{d[5:]}", f"{d[:2]}-{d[2:]}"}
    return {hashlib.sha256(f.encode()).hexdigest() for f in forms if f}


def _local_problem(field: str, code: str, message: str) -> dict:
    return {"field": field, "code": code, "message": message, "source": "hollisworks"}


def build_request(crm: dict, member_ids: list[str], body: dict, today: date) -> tuple[list[dict], list[dict]]:
    """→ (contacts to send — one per owner, in owner order; local problems). A contact body carries the tax id and the
    date of birth: it is passed straight to the client and never logged, stored or returned."""
    contacts, probs = [], []
    members = set(member_ids)
    for i, o in enumerate(body.get("owners") or []):
        p = f"owners[{i}]"
        eid = str(o.get("entity_id") or "")
        ov = o.get("overrides") or {}
        if eid not in members or eid not in crm["entities"]:
            probs.append(_local_problem(f"{p}.entity_id", "not_a_member", "the owner must be a member of this "
                                                                          "household (link them first)"))
            contacts.append(None)
            continue
        e = crm["entities"][eid]
        pre = _member_prefill(eid, crm, today)
        c = {"client_reference": f"hw-{eid}", "kind": pre["kind"]}
        for f in ("legal_name", "email", "phone", "residential_address", "mailing_address", "citizenship",
                  "employment"):
            c[f] = ov[f] if f in ov and ov[f] not in (None, "", {}) else pre[f]
        if pre["kind"] == "person":
            c["visa_type"] = ov.get("visa_type")
            c["visa_expiration"] = ov.get("visa_expiration")
        dob = ov.get("date_of_birth") or (e["date_of_birth"].isoformat() if e.get("date_of_birth") else None)
        if dob:
            c["date_of_birth"] = dob
        taxes = crm["tax_ids"].get(eid) or []
        typed = ov.get("tax_id") if isinstance(ov.get("tax_id"), dict) else None
        if typed and _digits(typed.get("value")):
            if taxes and not (_crm_hash_candidates(str(typed["value"]))
                              & {t["tax_id_encrypted"] for t in taxes}):
                probs.append(_local_problem(f"{p}.tax_id", "crm_mismatch",
                                            "does not match the tax ID on the CRM record ("
                                            + mask(taxes[0]["tax_id_last4"]) + ")"))
            c["tax_id"] = {"kind": typed.get("kind") or (taxes[0]["kind"] if taxes else None),
                           "value": str(typed["value"])}
        elif len(_digits(e.get("tax_id"))) == 9:
            k = (typed or {}).get("kind") or (taxes[0]["kind"] if taxes else None) or _infer_kind(
                pre["kind"], _digits(e["tax_id"]))
            c["tax_id"] = {"kind": k, "value": _digits(e["tax_id"])}
        elif taxes:
            probs.append(_local_problem(f"{p}.tax_id", "type_tax_id", "the CRM keeps only the last four of this tax "
                                        "ID (" + mask(taxes[0]["tax_id_last4"]) + ") — type the full number for this "
                                        "request (it is sent once and not stored)"))
        contacts.append(c)
    return contacts, probs


def _beneficiaries(crm: dict, body: dict) -> list[dict]:
    out = []
    for b in body.get("beneficiaries") or []:
        name = b.get("name")
        kind = b.get("kind")
        if b.get("entity_id") and str(b["entity_id"]) in crm["names"]:
            name = name or crm["names"][str(b["entity_id"])]
        if b.get("entity_id") and str(b["entity_id"]) in crm["entities"]:
            name = name or crm["entities"][str(b["entity_id"])]["display_name"]
            kind = kind or _kind(crm["entities"][str(b["entity_id"])]["entity_type"])
        out.append({"tier": b.get("tier"), "name": name, "kind": kind, "relationship": b.get("relationship"),
                    "percentage": None if b.get("percentage") in (None, "") else str(b["percentage"])})
    return out


def _trusted(crm: dict, body: dict) -> dict | None:
    t = body.get("trusted_contact")
    if not t:
        return None
    t = dict(t)
    eid = str(t.pop("entity_id", "") or "")
    if eid and eid in crm["entities"]:
        e = crm["entities"][eid]
        t.setdefault("name", e["display_name"])
        t["name"] = t.get("name") or e["display_name"]
        t["email"] = t.get("email") or e.get("primary_email")
        t["phone"] = t.get("phone") or e.get("primary_phone")
    return {k: t.get(k) for k in ("name", "email", "phone", "relationship")}


def _redacted_problems(probs: list) -> list[dict]:
    """For audit: field + code only (never a value; messages from the counterparty never carry one either)."""
    return [{"field": str(p.get("field"))[:120], "code": str(p.get("code"))[:60]} for p in probs or []]


async def _audit(conn, org_id, actor_id, action: str, household_id, payload: dict) -> None:
    await HA._audit(conn, org_id, actor_id, action, household_id, payload)


async def _prepare(pool, *, org_id, household_id, body: dict, environment: str, today: date):
    """Guard + CRM read + the request + the Realtime session, on ONE connection that is released before any HTTP."""
    t = body.get("account_type")
    async with pool.acquire() as conn:
        hh, member_ids = await _members(conn, org_id, household_id)
        avail = await availability(conn, org_id, hh["id"], environment)
        if not avail["available"]:
            raise OpeningRefused(avail["reason"])
        crm = await _crm(conn, org_id, member_ids)
        vocab = await account_vocab(conn, org_id)
        sess = await RS.session(conn, org_id=_u(org_id), environment=environment)
    contacts, probs = build_request(crm, member_ids, body, today)
    if t in UNSUPPORTED_TYPES:
        probs.append(_local_problem("account_type", "unsupported_type", f"{UNSUPPORTED_TYPES[t]}: {UNSUPPORTED_REASON}"))
    elif t not in ACCOUNT_TYPES:
        probs.append(_local_problem("account_type", "unknown_type", "choose one of the custodian's supported account "
                                                                    "types"))
    return hh, crm, member_ids, vocab, sess, contacts, probs


async def _send_draft(sess, hh: dict, body: dict, crm: dict, contacts: list, transport: Any) -> tuple[dict, list]:
    """household → contacts → the draft. Returns (the draft, contact problems the counterparty already reported)."""
    house = await RS.household(sess, f"{hh['name']} · HW-{str(hh['id'])[:8]}", transport=transport)
    owners = []
    for o, c in zip(body.get("owners") or [], contacts):
        if c is None:
            owners.append({"contact_id": None, "role": o.get("role")})
            continue
        made = await RS.contact(sess, c, transport=transport)
        owners.append({"contact_id": made["id"], "role": o.get("role")})
    ref = body.get("draft_reference") or str(uuid.uuid4())
    d = await RS.draft(sess, {"client_reference": ref, "household_id": house["id"],
                              "account_type": body.get("account_type"), "account_group": body.get("account_group"),
                              "owners": owners, "beneficiaries": _beneficiaries(crm, body),
                              "trusted_contact": _trusted(crm, body)}, transport=transport)
    return {**d, "draft_reference": ref}, owners


async def check(pool, *, org_id, actor_id, household_id, body: dict, environment: str = SIMULATOR,
                transport: Any = None, today: date | None = None) -> dict:
    """Dry run: the counterparty's validation with no opening. Writes nothing in Hollisworks."""
    today = today or date.today()
    hh, crm, member_ids, vocab, sess, contacts, local = await _prepare(
        pool, org_id=org_id, household_id=household_id, body=body, environment=environment, today=today)
    if body.get("account_type") in UNSUPPORTED_TYPES:
        return {"ok": False, "problems": local, "draft_reference": body.get("draft_reference"), "label": LABEL}
    d, _ = await _send_draft(sess, hh, body, crm, contacts, transport)
    v = await RS.validate(sess, d["id"], transport=transport)
    probs = local + [{**p, "source": "custodian"} for p in v.get("problems") or []]
    return {"ok": not probs, "problems": probs, "draft_reference": d["draft_reference"], "label": LABEL,
            "status": v.get("status")}


async def _existing(conn, org_id, external_id: str) -> dict | None:
    from services.portfolio_assets import _OrgWrite

    async with _OrgWrite(conn, _u(org_id)) as c:
        r = await c.fetchrow(
            "SELECT a.id, a.account_number_masked, a.household_id FROM portfolio.external_references x JOIN accounts a ON "
            "a.id = x.record_id WHERE x.org_id = $1::uuid AND x.source_system = $2 AND x.external_id = $3 AND "
            "x.record_type = $4 AND a.system_to IS NULL", _u(org_id), XWALK_SOURCE, external_id, RECORD_TYPE)
    return dict(r) if r else None


async def open_account(pool, *, org_id, actor_id, household_id, body: dict, environment: str = SIMULATOR,
                       transport: Any = None, today: date | None = None) -> dict:
    """Open at the simulated custodian; on success record it through add_account + the crosswalk, then sync it."""
    from services import altruist_positions_sync as SYNC
    from services.portfolio_assets import upsert_external_reference

    today = today or date.today()
    t = body.get("account_type")
    hh, crm, member_ids, vocab, sess, contacts, local = await _prepare(
        pool, org_id=org_id, household_id=household_id, body=body, environment=environment, today=today)
    if local:
        async with pool.acquire() as conn:
            await _audit(conn, org_id, actor_id, "custodian_account_refused", hh["id"],
                         {"account_type": t, "problems": _redacted_problems(local), "sent": False, "label": LABEL})
        return {"ok": False, "opened": False, "problems": local, "draft_reference": body.get("draft_reference"),
                "label": LABEL}
    d, owners = await _send_draft(sess, hh, body, crm, contacts, transport)
    sub = await RS.submit(sess, d["id"], transport=transport)
    if not sub["opened"]:
        probs = [{**p, "source": "custodian"} for p in sub["problems"]]
        async with pool.acquire() as conn:
            await _audit(conn, org_id, actor_id, "custodian_account_refused", hh["id"],
                         {"account_type": t, "draft_id": d["id"], "problems": _redacted_problems(probs), "sent": True,
                          "label": LABEL})
        return {"ok": False, "opened": False, "problems": probs, "draft_reference": d["draft_reference"],
                "label": LABEL}
    acct = sub["account"]
    ext_id, number = acct["id"], acct["account_number"]
    shape = ACCOUNT_TYPES[t][1]
    primary_role = PRIMARY_ROLE[shape]
    primary = next((o for o in body.get("owners") or [] if o.get("role") == primary_role), None)
    reg_opt = next(o for o in account_type_options(vocab)["supported"] if o["key"] == t)
    async with pool.acquire() as conn:
        found = await _existing(conn, org_id, ext_id)
    created = None
    if found is None:
        async def link(conn, row):
            await upsert_external_reference(conn, org_id=_u(org_id), source_system=XWALK_SOURCE, external_id=ext_id,
                                            record_type=RECORD_TYPE, record_id=str(row["id"]))
        authority, custody = AUTH.default_for({"is_held_away": False})
        try:
            created = await HA.add_account(
                pool, org_id=org_id, actor_id=actor_id, household_id=hh["id"], custodian_code=CUSTODIAN_CODE,
                account_number=number, primary_entity_id=primary["entity_id"], authority=authority, custody=custody,
                registration_type=reg_opt["registration_type"], tax_status=reg_opt["tax_status"],
                add_owner_as_member=True, on_created=link, today=today)
        except HA.HouseholdAdminConflict:
            async with pool.acquire() as conn:
                found = await _existing(conn, org_id, ext_id)
            if found is None:
                raise
    account_id = created["id"] if created else str(found["id"])
    masked = created["account_number_masked"] if created else found["account_number_masked"]
    sync = {"synced": False, "reason": None}
    try:
        async with pool.acquire() as conn:
            res = await SYNC.sync_resolved_accounts(conn, org_id=_u(org_id), environment=SIMULATOR,
                                                    as_of_date=today, transport=transport,
                                                    only_account_ids=[account_id])
        sync = {"synced": account_id not in res.refused_accounts and res.accounts_synced == 1,
                "accounts_synced": res.accounts_synced,
                "reason": "refused: not a demo household" if account_id in res.refused_accounts else None}
    except AltruistOAuthError as exc:
        sync = {"synced": False, "reason": f"not synced yet — {exc}"}
    async with pool.acquire() as conn:
        await _audit(conn, org_id, actor_id, "custodian_account_opened", hh["id"],
                     {"account_id": account_id, "account": masked, "custodian": CUSTODIAN_CODE,
                      "counterparty_account_id": ext_id, "account_type": t, "draft_id": d["id"],
                      "date_open": acct.get("date_open"), "repeat": created is None, "synced": sync.get("synced"),
                      "label": LABEL})
    return {"ok": True, "opened": True, "repeat": created is None, "label": LABEL,
            "draft_reference": d["draft_reference"],
            "account": {"id": account_id, "account_number_masked": masked, "custodian_code": CUSTODIAN_CODE,
                        "registration_type": reg_opt["registration_type"], "tax_status": reg_opt["tax_status"],
                        "primary_entity_id": primary["entity_id"]},
            "counterparty": {"account_id": ext_id, "account_type": acct.get("account_type"),
                             "status": acct.get("status"), "date_open": acct.get("date_open"),
                             "account_number_masked": mask(last4(number))},
            "sync": sync, "problems": []}
