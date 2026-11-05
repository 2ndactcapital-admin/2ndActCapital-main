"""PERMS1 — creating households and adding accounts to them (Joe's decision REP-01, 2026-11-04).

Who: internal firm roles holding ``manage_members`` (the org admin holds it since PERMS1; support staff does not).
Never a member — the router gates ``require_internal`` + ``manage_members`` before anything here runs.

What, and what it reuses (no second mechanism):
  * a household = the ``households`` row of ``services.households`` (SOC Phase 3) — the SAME table
    ``accounts.household_id`` and REP7B's household authority point at — plus its ``household_memberships``
    rows for member entities picked from the CRM (``entities`` of the org; the operational types ``account``
    and ``spv`` are refused — they are not people or families);
  * an account added by hand = one ``accounts`` row carrying ONLY the mask and the keyed hash of the typed
    number, exactly as the custody import stores it (``services.custody.base.AccountNumber.masked`` /
    ``.hashed`` with the org salt from ``services.custody.registry.get_or_create_salt``). The full number is
    never stored, returned or audited. The same live (org, custodian, hash) unique key the import upserts on
    means a hand-added account and a later import of the same account converge on one row — the import keeps
    working unchanged;
  * authority / custody: the account starts at REP7B's default for its kind (``authority.default_for``:
    held away → read-only / outside, otherwise managed / integrated). A different choice is NOT written
    directly: it becomes REP7B's draft account setting + approval task (``authority.request_account_change``,
    maker-checker), so adding an account never bypasses the second person.

Every write leaves an ``audit_log`` row (``household.created`` / ``household.account_added``).
"""

from __future__ import annotations

import json
from datetime import date
from uuid import UUID

from services import authority as AUTH
from services.crm_config import account_vocab
from services.custody.base import UNKNOWN, AccountNumber
from services.custody.registry import get_or_create_salt

AUDIT_RESOURCE = "household"
REFUSED_ENTITY_TYPES = ("account", "spv")
PERSON_TYPES = ("individual",)
#: PORTENTRY2 — the account columns a household shows and edits (accounts is system-axis: its id is an FK target).
ACCOUNT_COLS = ("id, account_number_masked, custodian_code, registration_type, tax_status, primary_entity_id, "
                "is_held_away, is_billable, is_discretionary, service_model")
EDITABLE_ACCOUNT_FIELDS = ("registration_type", "tax_status", "is_billable", "is_discretionary", "service_model")


class HouseholdAdminError(Exception):
    """A refusal with a plain reason (→ 422)."""


class HouseholdAdminConflict(Exception):
    """The request clashes with what exists (→ 409)."""


class HouseholdNotFound(Exception):
    """No such household in the caller's org (→ 404)."""


def _u(v) -> UUID:
    return v if isinstance(v, UUID) else UUID(str(v))


async def _audit(conn, org_id, user_id, action: str, resource_id, payload: dict) -> None:
    await conn.execute(
        "INSERT INTO audit_log (org_id, user_id, action, resource_type, resource_id, payload) "
        "VALUES ($1::uuid, $2::uuid, $3, $4, $5::uuid, $6::jsonb)",
        str(org_id), str(user_id) if user_id else None, f"household.{action}", AUDIT_RESOURCE, str(resource_id),
        json.dumps(payload, default=str))


async def _member_entities(conn, org_id, entity_ids) -> list[dict]:
    ids = list(dict.fromkeys(_u(e) for e in entity_ids))
    if not ids:
        return []
    rows = await conn.fetch(
        "SELECT id, display_name, entity_type::text AS entity_type FROM entities "
        "WHERE org_id = $1 AND id = ANY($2::uuid[])", _u(org_id), ids)
    found = {r["id"]: dict(r) for r in rows}
    missing = [str(i) for i in ids if i not in found]
    if missing:
        raise HouseholdAdminError(f"no CRM entity in this organisation with id {', '.join(missing)}")
    bad = [found[i]["display_name"] for i in ids if found[i]["entity_type"] in REFUSED_ENTITY_TYPES]
    if bad:
        raise HouseholdAdminError(f"{', '.join(bad)}: an account or SPV record is not a household member — "
                                  "link the person, trust or company instead")
    return [found[i] for i in ids]


async def create_household(conn, *, org_id, actor_id, name: str, member_entity_ids) -> dict:
    """One transaction: the household, its member links, its audit row."""
    name = (name or "").strip()
    if not name:
        raise HouseholdAdminError("a household needs a name")
    async with conn.transaction():
        members = await _member_entities(conn, org_id, member_entity_ids or [])
        row = await conn.fetchrow(
            "INSERT INTO households (org_id, name, created_by) VALUES ($1, $2, $3) RETURNING id, name, created_at",
            _u(org_id), name, _u(actor_id) if actor_id else None)
        for m in members:
            await conn.execute(
                "INSERT INTO household_memberships (household_id, entity_id, added_by) VALUES ($1, $2, $3) "
                "ON CONFLICT (household_id, entity_id) DO NOTHING",
                row["id"], m["id"], _u(actor_id) if actor_id else None)
        await _audit(conn, org_id, actor_id, "created", row["id"],
                     {"name": name, "members": [str(m["id"]) for m in members]})
    return {"id": str(row["id"]), "name": row["name"],
            "members": [{"id": str(m["id"]), "display_name": m["display_name"], "entity_type": m["entity_type"]}
                        for m in members]}


async def _household(conn, org_id, household_id) -> dict:
    row = await conn.fetchrow("SELECT id, name FROM households WHERE id = $1 AND org_id = $2",
                              _u(household_id), _u(org_id))
    if row is None:
        raise HouseholdNotFound("Household not found")
    return dict(row)


async def list_households(conn, org_id) -> list[dict]:
    rows = await conn.fetch(
        """
        SELECT h.id, h.name, h.created_at,
               (SELECT count(*) FROM household_memberships m WHERE m.household_id = h.id) AS members,
               (SELECT count(*) FROM accounts a WHERE a.household_id = h.id AND a.org_id = h.org_id
                  AND a.system_to IS NULL AND a.valid_to IS NULL) AS accounts
          FROM households h WHERE h.org_id = $1 ORDER BY h.name
        """, _u(org_id))
    return [{"id": str(r["id"]), "name": r["name"], "members": int(r["members"]), "accounts": int(r["accounts"])}
            for r in rows]


async def household_detail(conn, org_id, household_id) -> dict:
    hh = await _household(conn, org_id, household_id)
    members = await conn.fetch(
        "SELECT e.id, e.display_name, e.entity_type::text AS entity_type FROM household_memberships m "
        "JOIN entities e ON e.id = m.entity_id WHERE m.household_id = $1 AND e.org_id = $2 ORDER BY e.display_name",
        hh["id"], _u(org_id))
    accounts = await conn.fetch(
        f"SELECT {ACCOUNT_COLS} FROM accounts WHERE household_id = $1 AND org_id = $2 AND system_to IS NULL "
        "AND valid_to IS NULL ORDER BY account_number_masked", hh["id"], _u(org_id))
    # CRMDETAIL1 — the household's Liabilities section and its net worth as three numbers (gross, liabilities,
    # net), from the same member set and the same look-through basis as the flexible rollup.
    from services import households as HH
    from services import liabilities as L

    member_ids = [str(m["id"]) for m in members]
    liab_rows = await conn.fetch(
        f"SELECT {L.COLS} FROM {L.TABLE} WHERE org_id = $1 AND owner_entity_id = ANY($2::uuid[]) "
        "AND valid_to IS NULL AND system_to IS NULL ORDER BY name", _u(org_id), [m["id"] for m in members])
    names = await L._names(conn, org_id, liab_rows)
    owner_names = {str(m["id"]): m["display_name"] for m in members}
    liabilities = [{**L.to_json(r, names), "owner_name": owner_names.get(str(r["owner_entity_id"]))}
                   for r in liab_rows]
    gross = (await HH._sum_holdings(conn, org_id, member_ids, None))["total_value"]
    net_worth = await HH._net_worth(conn, org_id, member_ids, gross, None)
    vocab = await account_vocab(conn, org_id)
    member_list = [{"id": str(m["id"]), "display_name": m["display_name"], "entity_type": m["entity_type"]}
                   for m in members]
    return {"id": str(hh["id"]), "name": hh["name"],
            "liabilities": liabilities, "net_worth": net_worth,
            "is_demo": bool(await conn.fetchval("SELECT public.crm_is_demo_household($1)", hh["id"])),
            "members": member_list,
            "member_count": len(member_list),
            # PORTENTRY2 (H-02) — "add a legal entity owned by a member": the org's legal entities a member owns
            # (either ownership table) that are not members yet; choosing one as an account owner links it too.
            "owner_candidates": await owner_candidates(conn, org_id, hh["id"], [m["id"] for m in members]),
            # BUGFIX3 (MUSE-OLIVO-M, Task 4) — approved integrated-import rows of the members: LIVE positions,
            # labelled provisional until the custodian feed closes them
            "provisional_holdings": await provisional_holdings(conn, org_id, member_ids),
            "provisional_label": PROVISIONAL_LABEL,
            "account_count": len(accounts),
            "accounts": [account_json(a, vocab, {m["id"]: m["display_name"] for m in member_list})
                         for a in accounts]}


def _label(options: list[dict], key):
    return next((o["label"] for o in options if o["key"] == key), None)


def account_json(a, vocab: dict, owner_names: dict) -> dict:
    """One account as the household screens show it — masked number only, labels from the org's config."""
    reg, tax = a["registration_type"], a["tax_status"]
    return {"id": str(a["id"]), "account_number_masked": a["account_number_masked"],
            "custodian_code": a["custodian_code"],
            "registration_type": reg, "registration_type_label": _label(vocab["registration_types"], reg),
            "tax_status": tax, "tax_status_label": _label(vocab["tax_statuses"], tax),
            "primary_entity_id": str(a["primary_entity_id"]),
            "owner_name": owner_names.get(str(a["primary_entity_id"])),
            "is_held_away": bool(a["is_held_away"]), "is_billable": bool(a["is_billable"]),
            "is_discretionary": bool(a["is_discretionary"]), "service_model": a["service_model"]}


async def owner_candidates(conn, org_id, household_id, member_ids) -> list[dict]:
    """Legal entities of the org that a member owns directly (BOTH ownership tables, services.ownership_read) and
    that are not members yet. Operational types (account, spv) and people are never offered."""
    from services.ownership_read import ownership_edges

    members = {_u(m) for m in member_ids}
    if not members:
        return []
    edges = await ownership_edges(conn, _u(org_id), list(members))
    owned = {e["child_id"]: e["parent_id"] for e in edges if e["parent_id"] in members and e["child_id"] not in members}
    if not owned:
        return []
    rows = await conn.fetch(
        "SELECT id, display_name, entity_type::text AS entity_type FROM entities WHERE org_id = $1 "
        "AND id = ANY($2::uuid[]) AND valid_to IS NULL AND system_to IS NULL "
        "AND entity_type::text <> ALL($3::text[]) ORDER BY display_name",
        _u(org_id), list(owned), list(REFUSED_ENTITY_TYPES + PERSON_TYPES))
    names = {r["id"]: r["display_name"] for r in await conn.fetch(
        "SELECT id, display_name FROM entities WHERE org_id = $1 AND id = ANY($2::uuid[]) AND valid_to IS NULL "
        "AND system_to IS NULL", _u(org_id), list(members))}
    return [{"id": str(r["id"]), "display_name": r["display_name"], "entity_type": r["entity_type"],
             "owned_by": str(owned[r["id"]]), "owned_by_name": names.get(owned[r["id"]])} for r in rows]


async def add_member(conn, *, org_id, actor_id, household_id, entity_id) -> dict:
    """PORTENTRY2 (MUSE-OLIVO-H-02 / G-02) — link a person OR a legal entity to an EXISTING household, audited.

    Membership is many-to-many (household_memberships PK (household_id, entity_id)): an entity may sit in several
    households, as SOC Phase 3 designed — net worth / billing that must not double count use the strict
    entities.primary_household_id, which this does not touch."""
    async with conn.transaction():
        hh = await _household(conn, org_id, household_id)
        member = (await _member_entities(conn, org_id, [entity_id]))[0]
        inserted = await conn.fetchval(
            "INSERT INTO household_memberships (household_id, entity_id, added_by) VALUES ($1, $2, $3) "
            "ON CONFLICT (household_id, entity_id) DO NOTHING RETURNING entity_id",
            hh["id"], member["id"], _u(actor_id) if actor_id else None)
        if inserted is None:
            raise HouseholdAdminConflict(f"{member['display_name']} is already a member of {hh['name']}")
        await _audit(conn, org_id, actor_id, "member_added", hh["id"],
                     {"entity_id": str(member["id"]), "entity_type": member["entity_type"]})
    return {"household_id": str(hh["id"]), "id": str(member["id"]), "display_name": member["display_name"],
            "entity_type": member["entity_type"]}


async def remove_member(conn, *, org_id, actor_id, household_id, entity_id) -> dict:
    """Unlink a member, audited. Refused (409, naming the accounts) while it owns a live account of this household —
    the account's owner must stay a member. A demo household's member is removed through the app path crmdetail1
    provides (services.households.remove_entity_from_household sets the transaction-local crm.demo_edit GUC the
    delete guard admits); nothing here bypasses the guard any other way."""
    from services import households as HH

    async with conn.transaction():
        hh = await _household(conn, org_id, household_id)
        eid = _u(entity_id)
        owning = await conn.fetch(
            "SELECT account_number_masked FROM accounts WHERE org_id = $1 AND household_id = $2 "
            "AND primary_entity_id = $3 AND system_to IS NULL AND valid_to IS NULL ORDER BY 1",
            _u(org_id), hh["id"], eid)
        if owning:
            raise HouseholdAdminConflict(
                "this member owns " + ", ".join(r["account_number_masked"] for r in owning)
                + " in this household — move or close those accounts before removing the member")
        removed = await HH.remove_entity_from_household(conn, hh["id"], eid)
        if not removed:
            raise HouseholdNotFound("that record is not a member of this household")
        await _audit(conn, org_id, actor_id, "member_removed", hh["id"], {"entity_id": str(eid)})
    return {"household_id": str(hh["id"]), "id": str(eid), "removed": True}


def dvp_missing(custody: str, receiving: dict | None) -> list[str]:
    """MUSE-OLIVO-H-03 / H-04 — the receiving-custodian fields a DVP ("another custodian") account still needs."""
    if custody != "other_custodian":
        return []
    rc = receiving or {}
    missing = []
    if not str(rc.get("custodian_name") or "").strip():
        missing.append("receiving custodian")
    if not str(rc.get("participant_number") or "").strip():
        missing.append("DTC / participant number")
    return missing


def _check_vocab(vocab: dict, registration_type, tax_status) -> None:
    errors = []
    if registration_type not in (None, ""):
        allowed = [o["key"] for o in vocab["registration_types"]]
        if registration_type not in allowed:
            errors.append(f"registration type '{registration_type}' is not one of {', '.join(allowed)}")
    if tax_status not in (None, ""):
        allowed = [o["key"] for o in vocab["tax_statuses"]]
        if tax_status not in allowed:
            errors.append(f"tax status '{tax_status}' is not one of {', '.join(allowed)}")
    if errors:
        raise HouseholdAdminError("; ".join(errors))


async def add_account(pool, *, org_id, actor_id, household_id, custodian_code: str, account_number: str,
                      primary_entity_id, authority: str, custody: str, registration_type: str | None = None,
                      tax_status: str | None = None, reason: str | None = None,
                      receiving: dict | None = None, today: date | None = None,
                      is_billable: bool | None = None, is_discretionary: bool | None = None,
                      service_model: str | None = None, add_owner_as_member: bool = False) -> dict:
    """Insert the account (mask + hash only) at REP7B's default; a non-default authority / custody becomes a
    REP7B draft change awaiting a second person. Returns the account (masked) and the pending change, if any.

    PORTENTRY2: the owner is any household member — a person or a legal entity (H-02). With
    ``add_owner_as_member`` a legal entity a member owns (``owner_candidates``) is linked as a member in the same
    transaction. Registration type / tax status come from the org's config vocabulary (H-01); ``is_billable``
    defaults OFF for a demo household (every member tagged demo-*), ON otherwise."""
    today = today or date.today()
    custodian_code = (custodian_code or "").strip()
    if not custodian_code:
        raise HouseholdAdminError("an account needs its custodian")
    if authority not in AUTH.AUTHORITIES:
        raise HouseholdAdminError(f"authority is one of {list(AUTH.AUTHORITIES)}")
    if custody not in AUTH.CUSTODIES:
        raise HouseholdAdminError(f"custody is one of {list(AUTH.CUSTODIES)}")
    if not AUTH.valid_setting(authority, custody):
        raise HouseholdAdminError("managed needs a custodian: an account held outside cannot be managed — choose "
                                  "read-only or advise, or another custodian")
    try:
        number = AccountNumber(account_number)
    except ValueError as exc:
        raise HouseholdAdminError("type the account number (it is stored masked, with a keyed hash)") from exc
    missing = dvp_missing(custody, receiving)
    if missing:
        raise HouseholdAdminError("another custodian (settled DVP) needs the " + " and the ".join(missing)
                                  + " — the receiving side of every delivery-versus-payment settlement")
    # REP7B: an account fed by the integrated custodian has custody "integrated"; anything else is held away
    # (its data does not come from the integrated feed) — authority.request_account_change enforces the same.
    is_held_away = custody != "integrated"
    account = {"is_held_away": is_held_away}
    default = AUTH.default_for(account)
    service_model = (service_model or "").strip() or None

    async with pool.acquire() as conn:
        async with conn.transaction():
            hh = await _household(conn, org_id, household_id)
            vocab = await account_vocab(conn, org_id)
            _check_vocab(vocab, registration_type, tax_status)
            owner = _u(primary_entity_id)
            linked = await conn.fetchval(
                "SELECT 1 FROM household_memberships m JOIN entities e ON e.id = m.entity_id "
                "WHERE m.household_id = $1 AND m.entity_id = $2 AND e.org_id = $3",
                hh["id"], owner, _u(org_id))
            linked_now = False
            if not linked:
                members = [r["entity_id"] for r in await conn.fetch(
                    "SELECT entity_id FROM household_memberships WHERE household_id = $1", hh["id"])]
                candidates = {c["id"] for c in await owner_candidates(conn, org_id, hh["id"], members)}
                if not (add_owner_as_member and str(owner) in candidates):
                    raise HouseholdAdminError("the account's owner must be a member of this household, or a legal "
                                              "entity a member owns (chosen with 'add as a member') — link them first")
                await conn.execute(
                    "INSERT INTO household_memberships (household_id, entity_id, added_by) VALUES ($1, $2, $3)",
                    hh["id"], owner, _u(actor_id) if actor_id else None)
                await _audit(conn, org_id, actor_id, "member_added", hh["id"],
                             {"entity_id": str(owner), "with_account": True})
                linked_now = True
            if is_billable is None:
                is_billable = not bool(await conn.fetchval("SELECT public.crm_is_demo_household($1)", hh["id"]))
            if is_discretionary is None:
                is_discretionary = True
            salt = await get_or_create_salt(conn, org_id)
            digest = number.hashed(salt)
            existing = await conn.fetchval(
                "SELECT account_number_masked FROM accounts WHERE org_id = $1 AND custodian_code = $2 "
                "AND account_number_hash = $3 AND system_to IS NULL", _u(org_id), custodian_code, digest)
            if existing:
                raise HouseholdAdminConflict(f"{existing} at {custodian_code} is already an account of this "
                                             "organisation")
            row = await conn.fetchrow(
                """
                INSERT INTO accounts (org_id, account_number_masked, account_number_hash, custodian_code,
                                      registration_type, tax_status, primary_entity_id, household_id,
                                      is_held_away, created_by, is_billable, is_discretionary, service_model)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
                RETURNING id, account_number_masked
                """,
                _u(org_id), number.masked, digest, custodian_code, (registration_type or "").strip() or UNKNOWN,
                (tax_status or "").strip() or UNKNOWN, owner, hh["id"], is_held_away,
                _u(actor_id) if actor_id else None, bool(is_billable), bool(is_discretionary), service_model)
            await _audit(conn, org_id, actor_id, "account_added", hh["id"],
                         {"account_id": str(row["id"]), "account": row["account_number_masked"],
                          "custodian": custodian_code, "authority": authority, "custody": custody,
                          "default": list(default), "owner": str(owner), "owner_linked_now": linked_now,
                          "registration_type": registration_type, "tax_status": tax_status,
                          "is_billable": bool(is_billable), "is_discretionary": bool(is_discretionary)})
    out = {"id": str(row["id"]), "account_number_masked": row["account_number_masked"],
           "custodian_code": custodian_code, "household_id": str(hh["id"]),
           "authority": default[0], "custody": default[1], "pending_change": None,
           "registration_type": (registration_type or "").strip() or UNKNOWN,
           "tax_status": (tax_status or "").strip() or UNKNOWN, "is_billable": bool(is_billable),
           "is_discretionary": bool(is_discretionary), "service_model": service_model,
           "primary_entity_id": str(owner), "owner_linked_now": linked_now}
    if (authority, custody) != default:
        change = await AUTH.request_account_change(
            pool, org_id=org_id, actor_id=actor_id, account_id=row["id"],
            authority_override=authority, custody=custody, effective_from=today,
            reason=(reason or "").strip() or "Set when the account was added to its household",
            receiving=receiving, today=today)
        out["pending_change"] = change
    return out


async def update_account(conn, *, org_id, actor_id, household_id, account_id, changes: dict) -> dict:
    """PORTENTRY2 (H-01) — correct an account's registration type, tax status, billable / discretionary flags or
    service model from the household. ``accounts.id`` is an FK target, so this archives on the SYSTEM axis exactly as
    the custody import does (services.custody.importer._write_account): the outgoing version is copied to a new row
    stamped ``system_to = now()`` and the live row keeps its id. Audited with the before / after values."""
    unknown = sorted(set(changes) - set(EDITABLE_ACCOUNT_FIELDS))
    if unknown:
        raise HouseholdAdminError(f"not editable here: {', '.join(unknown)}")
    async with conn.transaction():
        hh = await _household(conn, org_id, household_id)
        cur = await conn.fetchrow(
            f"SELECT {ACCOUNT_COLS} FROM accounts WHERE id = $1 AND org_id = $2 AND household_id = $3 "
            "AND system_to IS NULL AND valid_to IS NULL", _u(account_id), _u(org_id), hh["id"])
        if cur is None:
            raise HouseholdNotFound("Account not found in this household")
        vocab = await account_vocab(conn, org_id)
        _check_vocab(vocab, changes.get("registration_type"), changes.get("tax_status"))
        clean = {}
        for k, v in changes.items():
            if k in ("is_billable", "is_discretionary"):
                if not isinstance(v, bool):
                    raise HouseholdAdminError(f"{k} is true or false")
            elif k == "service_model":
                v = (str(v).strip() or None) if v is not None else None
            elif v in (None, ""):
                raise HouseholdAdminError(f"{k.replace('_', ' ')} cannot be blank")
            if cur[k] != v:
                clean[k] = v
        if clean:
            await conn.execute(
                """
                INSERT INTO accounts
                    (org_id, account_number_masked, account_number_hash, custodian_code, custodian_account_id,
                     registration_type, tax_status, primary_entity_id, household_id, advisor_of_record_id,
                     service_model, is_billable, is_discretionary, is_held_away, opened_on, closed_on,
                     base_currency, created_by, created_at, updated_at, valid_from, valid_to, system_from, system_to)
                SELECT a.org_id, a.account_number_masked, a.account_number_hash, a.custodian_code,
                       a.custodian_account_id, a.registration_type, a.tax_status, a.primary_entity_id,
                       a.household_id, a.advisor_of_record_id, a.service_model, a.is_billable, a.is_discretionary,
                       a.is_held_away, a.opened_on, a.closed_on, a.base_currency, a.created_by, a.created_at,
                       a.updated_at, a.valid_from, a.valid_to, a.system_from, now()
                FROM accounts a WHERE a.id = $1 AND a.org_id = $2 AND a.system_to IS NULL
                """, cur["id"], _u(org_id))
            ordered = [f for f in EDITABLE_ACCOUNT_FIELDS if f in clean]   # column names: literals of this module
            sets = ", ".join(f"{name} = ${i + 3}" for i, name in enumerate(ordered))
            await conn.execute(f"UPDATE accounts SET {sets}, updated_at = now() WHERE id = $1 AND org_id = $2 "
                               "AND system_to IS NULL", cur["id"], _u(org_id), *[clean[n] for n in ordered])
            await _audit(conn, org_id, actor_id, "account_updated", hh["id"],
                         {"account_id": str(cur["id"]), "before": {k: cur[k] for k in clean}, "after": clean})
        row = await conn.fetchrow(f"SELECT {ACCOUNT_COLS} FROM accounts WHERE id = $1 AND system_to IS NULL",
                                  cur["id"])
    return {**account_json(row, vocab, {}), "changed": sorted(clean)}


# ── BUGFIX3 (Task 4) — household Recalculate + provisional (one-time import) holdings ─────────────────────────────
PROVISIONAL_LABEL = "Provisional — until the custodian feed"


async def provisional_holdings(conn, org_id, entity_ids) -> list[dict]:
    """Live positions written by an APPROVED integrated import (portfolio.provisional_positions, live row) for
    ``entity_ids`` — the same rows the Positions grid shows with ``is_provisional``. Fixed-point money."""
    from services.money_text import fixed

    if not entity_ids:
        return []
    rows = await conn.fetch(
        "SELECT p.id::text AS position_id, p.owner_entity_id::text AS owner_entity_id, e.display_name AS owner_name, "
        "a.name AS asset_name, p.quantity, p.market_value, p.cost_basis, p.as_of_date, "
        "acct.account_number_masked, pp.import_id::text AS import_id "
        "FROM portfolio.provisional_positions pp "
        "JOIN portfolio.positions p ON p.id = pp.position_id AND p.org_id = pp.org_id "
        " AND p.valid_to IS NULL AND p.system_to IS NULL "
        "JOIN portfolio.assets a ON a.id = p.asset_id AND a.org_id = p.org_id AND a.system_to IS NULL "
        "JOIN entities e ON e.id = p.owner_entity_id AND e.valid_to IS NULL AND e.system_to IS NULL "
        "LEFT JOIN accounts acct ON acct.id = pp.account_id AND acct.valid_to IS NULL AND acct.system_to IS NULL "
        "WHERE pp.org_id = $1 AND pp.valid_to IS NULL AND pp.system_to IS NULL "
        "AND p.owner_entity_id = ANY($2::uuid[]) ORDER BY e.display_name, a.name",
        _u(org_id), [_u(x) for x in entity_ids])
    return [{"position_id": r["position_id"], "owner_entity_id": r["owner_entity_id"], "owner_name": r["owner_name"],
             "asset_name": r["asset_name"], "quantity": fixed(r["quantity"]), "market_value": fixed(r["market_value"]),
             "cost_basis": fixed(r["cost_basis"]), "as_of_date": r["as_of_date"].isoformat(),
             "account_number_masked": r["account_number_masked"], "import_id": r["import_id"],
             "label": PROVISIONAL_LABEL} for r in rows]


async def recalculate_household(pool, org_id, household_id, *, actor_id) -> dict:
    """The household's Recalculate (MUSE-OLIVO-H): one family-scoped rollup over the household's members — the
    portentry1 refresh path (portfolio_refresh.request_rollup with no debounce, then run_request; the family is the
    members' household(s) + ownership graph, never org-wide) — then the household's net worth read back from the
    same look-through basis the household card shows."""
    from services import households as HH
    from services import portfolio_refresh as PR

    async with pool.acquire() as conn:
        hh = await _household(conn, org_id, household_id)
        member_ids = [str(r["entity_id"]) for r in await conn.fetch(
            "SELECT entity_id FROM household_memberships WHERE household_id = $1", hh["id"])]
    ran, req = None, {}
    if member_ids:
        async with pool.acquire() as conn:
            req = await PR.request_rollup(conn, org_id, member_ids, reason=f"household recalculate {hh['id']}",
                                          actor_id=actor_id, delay_s=0)
            if req:
                ran = await PR.run_request(conn, org_id, req["id"])
    async with pool.acquire() as conn:
        gross = (await HH._sum_holdings(conn, org_id, member_ids, None))["total_value"] if member_ids else None
        net_worth = await HH._net_worth(conn, org_id, member_ids, gross, None) if member_ids else None
        await _audit(conn, org_id, actor_id, "recalculate", hh["id"],
                     {"members": len(member_ids), "request_id": req.get("id") if req else None,
                      "status": (ran or {}).get("status")})
    return {"household_id": str(hh["id"]), "request_id": req.get("id") if req else None,
            "status": (ran or {}).get("status") or ("no members" if not member_ids else "not run"),
            "family_size": len(req.get("entity_ids") or []) if req else 0, "member_count": len(member_ids),
            "net_worth": net_worth}
