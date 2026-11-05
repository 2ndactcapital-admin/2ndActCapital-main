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
from services.custody.base import UNKNOWN, AccountNumber
from services.custody.registry import get_or_create_salt

AUDIT_RESOURCE = "household"
REFUSED_ENTITY_TYPES = ("account", "spv")


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
        "SELECT id, account_number_masked, custodian_code, registration_type, tax_status, primary_entity_id, "
        "is_held_away FROM accounts WHERE household_id = $1 AND org_id = $2 AND system_to IS NULL "
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
    return {"id": str(hh["id"]), "name": hh["name"],
            "liabilities": liabilities, "net_worth": net_worth,
            "members": [{"id": str(m["id"]), "display_name": m["display_name"], "entity_type": m["entity_type"]}
                        for m in members],
            "accounts": [{"id": str(a["id"]), "account_number_masked": a["account_number_masked"],
                          "custodian_code": a["custodian_code"], "registration_type": a["registration_type"],
                          "tax_status": a["tax_status"], "primary_entity_id": str(a["primary_entity_id"]),
                          "is_held_away": bool(a["is_held_away"])} for a in accounts]}


async def add_account(pool, *, org_id, actor_id, household_id, custodian_code: str, account_number: str,
                      primary_entity_id, authority: str, custody: str, registration_type: str | None = None,
                      tax_status: str | None = None, reason: str | None = None,
                      receiving: dict | None = None, today: date | None = None) -> dict:
    """Insert the account (mask + hash only) at REP7B's default; a non-default authority / custody becomes a
    REP7B draft change awaiting a second person. Returns the account (masked) and the pending change, if any."""
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
    if custody == "other_custodian":
        rc = receiving or {}
        if not (str(rc.get("custodian_name") or "").strip() and str(rc.get("participant_number") or "").strip()):
            raise HouseholdAdminError("another custodian needs the receiving custodian's name and its DTC / "
                                      "participant number")
    # REP7B: an account fed by the integrated custodian has custody "integrated"; anything else is held away
    # (its data does not come from the integrated feed) — authority.request_account_change enforces the same.
    is_held_away = custody != "integrated"
    account = {"is_held_away": is_held_away}
    default = AUTH.default_for(account)

    async with pool.acquire() as conn:
        async with conn.transaction():
            hh = await _household(conn, org_id, household_id)
            linked = await conn.fetchval(
                "SELECT 1 FROM household_memberships m JOIN entities e ON e.id = m.entity_id "
                "WHERE m.household_id = $1 AND m.entity_id = $2 AND e.org_id = $3",
                hh["id"], _u(primary_entity_id), _u(org_id))
            if not linked:
                raise HouseholdAdminError("the account's owner must be a member of this household — link them first")
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
                                      is_held_away, created_by)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                RETURNING id, account_number_masked
                """,
                _u(org_id), number.masked, digest, custodian_code, (registration_type or "").strip() or UNKNOWN,
                (tax_status or "").strip() or UNKNOWN, _u(primary_entity_id), hh["id"], is_held_away,
                _u(actor_id) if actor_id else None)
            await _audit(conn, org_id, actor_id, "account_added", hh["id"],
                         {"account_id": str(row["id"]), "account": row["account_number_masked"],
                          "custodian": custodian_code, "authority": authority, "custody": custody,
                          "default": list(default)})
    out = {"id": str(row["id"]), "account_number_masked": row["account_number_masked"],
           "custodian_code": custodian_code, "household_id": str(hh["id"]),
           "authority": default[0], "custody": default[1], "pending_change": None}
    if (authority, custody) != default:
        change = await AUTH.request_account_change(
            pool, org_id=org_id, actor_id=actor_id, account_id=row["id"],
            authority_override=authority, custody=custody, effective_from=today,
            reason=(reason or "").strip() or "Set when the account was added to its household",
            receiving=receiving, today=today)
        out["pending_change"] = change
    return out
