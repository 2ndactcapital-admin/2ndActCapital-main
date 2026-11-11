"""cpsim01 — the COUNTERPARTY REGISTRY: every read and write of the simulator's own book.

Every function takes a connection that is ALREADY inside a transaction AS ``cpsim_app`` (cpsim.db.tx() for the
sub-app, cpsim.db.as_simulator(conn) for the admin router). Nothing here touches a table outside ``cpsim``; the
security master / prices are read-only reference data for cpsim02+.

Bi-temporal (CLAUDE.md Rule 3):
  * ``cpsim.counterparties`` — SYSTEM axis: its id is referenced by every child table, so an edit archives a copy
    (``services.bitemporal.archive_live_row`` — the shared helper) and keeps the id.
  * FIX session, bank details, behaviour, clock — VALID axis: close the live row (valid_to = now()) and insert the new
    one; one live row per counterparty (partial unique indexes).
  * credentials — a credential is closed (rotated_at / revoked_at), never changed; the next one is a new row.

Secrets: a generated key / FIX password is returned ONCE in the result of the call that created it (``issued``) and
never stored or logged — only ``sha256(salt ‖ value)`` + the last four.
"""

from __future__ import annotations

from datetime import date, timedelta

import asyncpg

from cpsim import generators as G
from services.bitemporal import archive_live_row
from services.us_business_days import is_business_day, next_business_day

STATUSES = ("draft", "active", "disabled")
TRANSITIONS = {("draft", "active"), ("active", "disabled"), ("disabled", "active")}
OUTCOMES = ("accept", "reject", "partial", "delay")
BEGIN_STRINGS = ("FIX.4.2", "FIX.4.4", "FIXT.1.1")
FIX_FIELDS = ("begin_string", "sender_comp_id", "target_comp_id", "host", "port", "heartbt_int", "reset_on_logon",
              "username")
BEHAVIOUR_FIELDS = ("default_outcome", "reject_reason", "partial_pct", "delay_seconds", "ach_return_code",
                    "ach_return_after_days", "webhooks_enabled", "webhook_url")
BANK_GROUPS = {"bank_domestic": "domestic", "bank_international": "international"}
SETTING_ALLOWED = "counterparty_sim.allowed"
SETTING_COUNTERPARTY = "counterparty_sim.altruist_counterparty"     # cpsim02 D2: the slug an org's simulator talks to

_today = None   # injectable (tests): a zero-argument callable returning a date


def set_today(fn) -> None:
    global _today
    _today = fn


def today() -> date:
    return _today() if _today is not None else date.today()


class RegistryError(Exception):
    status = 400


class NotFound(RegistryError):
    status = 404


class Conflict(RegistryError):
    status = 409


def _d(row) -> dict | None:
    return dict(row) if row is not None else None


async def _guard(coro):
    """Translate the database's refusals into RegistryError naming the constraint (never echoing a value)."""
    try:
        return await coro
    except asyncpg.CheckViolationError as exc:
        raise RegistryError(f"refused by the database: {exc.constraint_name or 'check'} — "
                            f"{str(exc).split(' — ')[0][:200]}") from exc
    except asyncpg.UniqueViolationError as exc:
        raise Conflict(f"already exists ({exc.constraint_name})") from exc
    except asyncpg.NotNullViolationError as exc:
        raise RegistryError(f"a required field is missing ({exc.column_name})") from exc


# ── reads ───────────────────────────────────────────────────────────────────────────────────────────────────────
async def institution_types(conn) -> list[dict]:
    return [dict(r) for r in await conn.fetch(
        "SELECT key, label, protocol, field_groups, envelope_schema, description, sort_order "
        "FROM cpsim.institution_types ORDER BY sort_order, key")]


async def institution_type(conn, key: str) -> dict | None:
    return _d(await conn.fetchrow(
        "SELECT key, label, protocol, field_groups, envelope_schema, description FROM cpsim.institution_types "
        "WHERE key = $1", key))


async def live(conn, counterparty_id) -> dict | None:
    return _d(await conn.fetchrow(
        "SELECT * FROM cpsim.counterparties WHERE id = $1::uuid AND system_to IS NULL", str(counterparty_id)))


async def live_by_slug(conn, slug: str) -> dict | None:
    return _d(await conn.fetchrow(
        "SELECT * FROM cpsim.counterparties WHERE slug = $1 AND system_to IS NULL", slug))


async def _require(conn, counterparty_id) -> dict:
    row = await live(conn, counterparty_id)
    if row is None:
        raise NotFound("no such counterparty")
    return row


async def business_date(conn, counterparty_id) -> date | None:
    return await conn.fetchval("SELECT business_date FROM cpsim.counterparty_clock WHERE counterparty_id = $1::uuid "
                               "AND valid_to IS NULL", str(counterparty_id))


async def behaviour(conn, counterparty_id) -> dict | None:
    return _d(await conn.fetchrow(
        "SELECT default_outcome, reject_reason, partial_pct, delay_seconds, ach_return_code, ach_return_after_days, "
        "webhooks_enabled, webhook_url FROM cpsim.counterparty_behaviour WHERE counterparty_id = $1::uuid "
        "AND valid_to IS NULL", str(counterparty_id)))


async def list_counterparties(conn) -> list[dict]:
    rows = await conn.fetch(
        """SELECT c.id, c.slug, c.name, c.institution_type, t.label AS institution_type_label, c.status,
                  c.destination_path, c.created_at, c.system_from AS updated_at,
                  (SELECT k.last4 FROM cpsim.counterparty_credentials k WHERE k.counterparty_id = c.id
                     AND k.kind = 'api_key' AND k.rotated_at IS NULL AND k.revoked_at IS NULL) AS key_last4,
                  (SELECT cl.business_date FROM cpsim.counterparty_clock cl WHERE cl.counterparty_id = c.id
                     AND cl.valid_to IS NULL) AS business_date,
                  (SELECT max(l.at) FROM cpsim.request_log l WHERE l.counterparty_id = c.id) AS last_request_at
           FROM cpsim.counterparties c JOIN cpsim.institution_types t ON t.key = c.institution_type
           WHERE c.system_to IS NULL ORDER BY c.name, c.slug""")
    return [dict(r) for r in rows]


async def detail(conn, counterparty_id) -> dict:
    cp = await _require(conn, counterparty_id)
    cid = str(counterparty_id)
    typ = await institution_type(conn, cp["institution_type"])
    creds = [dict(r) for r in await conn.fetch(
        "SELECT id, kind, key_prefix, last4, created_at, rotated_at, revoked_at, CASE WHEN revoked_at IS NOT NULL THEN "
        "'revoked' WHEN rotated_at IS NOT NULL THEN 'rotated' ELSE 'live' END AS state "
        "FROM cpsim.counterparty_credentials WHERE counterparty_id = $1::uuid ORDER BY created_at DESC", cid)]
    fix = _d(await conn.fetchrow(
        "SELECT f.id, f.begin_string, f.sender_comp_id, f.target_comp_id, f.host, f.port, f.heartbt_int, "
        "f.reset_on_logon, f.username, f.valid_from, k.last4 AS password_last4 FROM cpsim.counterparty_fix_sessions f "
        "LEFT JOIN cpsim.counterparty_credentials k ON k.id = f.password_credential_id "
        "WHERE f.counterparty_id = $1::uuid AND f.valid_to IS NULL", cid))
    bank = [dict(r) for r in await conn.fetch(
        "SELECT id, kind, bank_name, aba_wire, aba_ach, account_number, bic, iban, intermediary_bank_name, "
        "intermediary_bic, intermediary_aba, beneficiary_name, beneficiary_address, valid_from "
        "FROM cpsim.counterparty_bank_details WHERE counterparty_id = $1::uuid AND valid_to IS NULL ORDER BY kind", cid)]
    behaviour = _d(await conn.fetchrow(
        "SELECT id, default_outcome, reject_reason, partial_pct, delay_seconds, ach_return_code, ach_return_after_days, "
        "webhooks_enabled, webhook_url, valid_from FROM cpsim.counterparty_behaviour WHERE counterparty_id = $1::uuid "
        "AND valid_to IS NULL", cid))
    clock = [dict(r) for r in await conn.fetch(
        "SELECT business_date, reason, valid_from, valid_to FROM cpsim.counterparty_clock WHERE counterparty_id = "
        "$1::uuid ORDER BY valid_from DESC LIMIT 20", cid)]
    history = [dict(r) for r in await conn.fetch(
        "SELECT id, name, status, notes, system_from, system_to, updated_by FROM cpsim.counterparties "
        "WHERE archived_from_id = $1::uuid ORDER BY system_to DESC LIMIT 50", cid)]
    return {"counterparty": cp, "institution_type": typ, "credentials": creds, "fix_session": fix,
            "bank_details": bank, "behaviour": behaviour, "clock": clock, "history": history}


async def request_log(conn, counterparty_id, limit: int = 50) -> list[dict]:
    return [dict(r) for r in await conn.fetch(
        "SELECT id, method, path, status, auth_outcome, shape_valid, error_count, message_type, direction, latency_ms, at "
        "FROM cpsim.request_log WHERE counterparty_id = $1::uuid ORDER BY at DESC LIMIT $2",
        str(counterparty_id), int(limit))]


# ── create / edit / status ──────────────────────────────────────────────────────────────────────────────────────
async def _insert_credential(conn, counterparty_id, secret: dict, kind: str, actor) -> str:
    return str(await _guard(conn.fetchval(
        "INSERT INTO cpsim.counterparty_credentials (counterparty_id, kind, key_prefix, key_hash, salt, last4, "
        "created_by) VALUES ($1::uuid, $2, $3, $4, $5, $6, $7::uuid) RETURNING id", str(counterparty_id), kind,
        secret["prefix"], secret["hash"], secret["salt"], secret["last4"], _uid(actor))))


def _uid(actor) -> str | None:
    return str(actor) if actor else None


async def _insert_bank(conn, counterparty_id, d: dict, actor) -> None:
    await _guard(conn.execute(
        "INSERT INTO cpsim.counterparty_bank_details (counterparty_id, kind, bank_name, aba_wire, aba_ach, "
        "account_number, bic, iban, intermediary_bank_name, intermediary_bic, intermediary_aba, beneficiary_name, "
        "beneficiary_address, created_by) VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, "
        "$14::uuid)", str(counterparty_id), d["kind"], d["bank_name"], d["aba_wire"], d["aba_ach"],
        d["account_number"], d["bic"], d["iban"], d["intermediary_bank_name"], d["intermediary_bic"],
        d["intermediary_aba"], d["beneficiary_name"], d["beneficiary_address"], _uid(actor)))


async def create(conn, *, name: str, institution_type: str, slug: str | None, actor) -> dict:
    typ = await institution_type_or_fail(conn, institution_type)
    slug = (slug or G.slugify(name)).strip()
    if await live_by_slug(conn, slug) is not None:
        raise Conflict(f"a counterparty with slug {slug!r} already exists")
    cid = str(await _guard(conn.fetchval(
        "INSERT INTO cpsim.counterparties (slug, name, institution_type, is_simulated, status, destination_path, "
        "created_by, updated_by) VALUES ($1, $2, $3, true, 'draft', '/cpsim/c/' || $1, $4::uuid, $4::uuid) "
        "RETURNING id", slug, name.strip(), typ["key"], _uid(actor))))
    await _guard(conn.execute(
        "INSERT INTO cpsim.counterparty_behaviour (counterparty_id, default_outcome, webhooks_enabled, created_by) "
        "VALUES ($1::uuid, 'accept', false, $2::uuid)", cid, _uid(actor)))
    await _guard(conn.execute(
        "INSERT INTO cpsim.counterparty_clock (counterparty_id, business_date, reason, set_by) VALUES ($1::uuid, $2, "
        "'initial', $3::uuid)", cid, next_business_day(today()), _uid(actor)))
    groups = set(typ["field_groups"])
    for group, kind in BANK_GROUPS.items():
        if group in groups:
            await _insert_bank(conn, cid, G.fake_bank_details(kind, name), actor)
    issued = {}
    key = G.new_api_key()
    await _insert_credential(conn, cid, key, "api_key", actor)
    issued["api_key"] = key["value"]
    if "fix" in groups:
        pw = G.new_fix_password()
        pw_id = await _insert_credential(conn, cid, pw, "fix_password", actor)
        await _guard(conn.execute(
            "INSERT INTO cpsim.counterparty_fix_sessions (counterparty_id, begin_string, sender_comp_id, "
            "target_comp_id, host, port, heartbt_int, reset_on_logon, password_credential_id, created_by) VALUES "
            "($1::uuid, 'FIX.4.4', 'HOLLISWORKS', $2, $3, 9878, 30, true, $4::uuid, $5::uuid)", cid,
            slug.upper().replace("-", "_")[:64], f"fix.{slug}.cpsim.invalid", pw_id, _uid(actor)))
        issued["fix_password"] = pw["value"]
    return {"id": cid, "slug": slug, "issued": issued}


async def institution_type_or_fail(conn, key: str) -> dict:
    typ = await institution_type(conn, key)
    if typ is None:
        raise RegistryError(f"unknown institution type {key!r}")
    return typ


async def edit(conn, counterparty_id, *, name: str | None, notes: str | None, actor) -> dict:
    cp = await _require(conn, counterparty_id)
    new_name = cp["name"] if name is None else name.strip()
    new_notes = cp["notes"] if notes is None else (notes.strip() or None)
    if new_name == cp["name"] and new_notes == cp["notes"]:
        return {"changed": False, "before": cp}
    await archive_live_row(conn, "cpsim.counterparties", cp["id"])
    await _guard(conn.execute(
        "UPDATE cpsim.counterparties SET name = $2, notes = $3, updated_by = $4::uuid, system_from = now() "
        "WHERE id = $1::uuid AND system_to IS NULL", str(cp["id"]), new_name, new_notes, _uid(actor)))
    return {"changed": True, "before": {"name": cp["name"], "notes": cp["notes"]},
            "after": {"name": new_name, "notes": new_notes}}


async def set_status(conn, counterparty_id, status: str, actor) -> dict:
    cp = await _require(conn, counterparty_id)
    if (cp["status"], status) not in TRANSITIONS:
        raise Conflict(f"cannot move a {cp['status']} counterparty to {status}")
    if status == "active":
        live_key = await conn.fetchval("SELECT count(*) FROM cpsim.counterparty_credentials WHERE counterparty_id = "
                                       "$1::uuid AND kind = 'api_key' AND rotated_at IS NULL AND revoked_at IS NULL",
                                       str(cp["id"]))
        if not live_key:
            raise Conflict("generate a key before activating")
    await archive_live_row(conn, "cpsim.counterparties", cp["id"])
    await conn.execute("UPDATE cpsim.counterparties SET status = $2, updated_by = $3::uuid, system_from = now() "
                       "WHERE id = $1::uuid AND system_to IS NULL", str(cp["id"]), status, _uid(actor))
    return {"from": cp["status"], "to": status}


# ── credentials ─────────────────────────────────────────────────────────────────────────────────────────────────
async def _live_credential(conn, counterparty_id, kind: str) -> dict | None:
    return _d(await conn.fetchrow(
        "SELECT * FROM cpsim.counterparty_credentials WHERE counterparty_id = $1::uuid AND kind = $2 AND "
        "rotated_at IS NULL AND revoked_at IS NULL", str(counterparty_id), kind))


async def generate_key(conn, counterparty_id, actor) -> dict:
    await _require(conn, counterparty_id)
    if await _live_credential(conn, counterparty_id, "api_key") is not None:
        raise Conflict("a live key exists — rotate it instead")
    key = G.new_api_key()
    cred = await _insert_credential(conn, counterparty_id, key, "api_key", actor)
    return {"credential_id": cred, "last4": key["last4"], "issued": {"api_key": key["value"]}}


async def _rotate(conn, counterparty_id, kind: str, secret: dict, actor) -> dict:
    old = await _live_credential(conn, counterparty_id, kind)
    if old is not None:
        await conn.execute("UPDATE cpsim.counterparty_credentials SET rotated_at = now() WHERE id = $1::uuid",
                           str(old["id"]))
    new_id = await _insert_credential(conn, counterparty_id, secret, kind, actor)
    if old is not None:
        await conn.execute("UPDATE cpsim.counterparty_credentials SET replaced_by = $2::uuid WHERE id = $1::uuid",
                           str(old["id"]), new_id)
    return {"credential_id": new_id, "replaced": str(old["id"]) if old else None, "last4": secret["last4"]}


async def rotate_key(conn, counterparty_id, actor) -> dict:
    await _require(conn, counterparty_id)
    if await _live_credential(conn, counterparty_id, "api_key") is None:
        raise Conflict("no live key to rotate — generate one")
    key = G.new_api_key()
    out = await _rotate(conn, counterparty_id, "api_key", key, actor)
    return {**out, "issued": {"api_key": key["value"]}}


async def revoke_key(conn, counterparty_id, actor) -> dict:
    await _require(conn, counterparty_id)
    old = await _live_credential(conn, counterparty_id, "api_key")
    if old is None:
        raise Conflict("no live key to revoke")
    await conn.execute("UPDATE cpsim.counterparty_credentials SET revoked_at = now(), revoked_by = $2::uuid "
                       "WHERE id = $1::uuid", str(old["id"]), _uid(actor))
    return {"credential_id": str(old["id"]), "last4": old["last4"]}


# ── valid-axis restatements ─────────────────────────────────────────────────────────────────────────────────────
async def _close_live(conn, table: str, counterparty_id, extra: str = "", *args) -> dict | None:
    return _d(await conn.fetchrow(
        f"UPDATE {table} SET valid_to = now() WHERE counterparty_id = $1::uuid AND valid_to IS NULL{extra} RETURNING *",
        str(counterparty_id), *args))


async def set_fix(conn, counterparty_id, fields: dict, actor, rotate_password: bool = False) -> dict:
    cp = await _require(conn, counterparty_id)
    typ = await institution_type_or_fail(conn, cp["institution_type"])
    if "fix" not in typ["field_groups"]:
        raise RegistryError(f"{typ['label']} has no FIX session")
    unknown = set(fields) - set(FIX_FIELDS)
    if unknown:
        raise RegistryError(f"unknown FIX fields {sorted(unknown)}")
    old = await _close_live(conn, "cpsim.counterparty_fix_sessions", cp["id"])
    base = {k: (old or {}).get(k) for k in FIX_FIELDS}
    base.setdefault("reset_on_logon", True)
    new = {**base, **fields}
    if new.get("begin_string") not in BEGIN_STRINGS:
        raise RegistryError(f"BeginString must be one of {', '.join(BEGIN_STRINGS)}")
    issued = {}
    pw_id = (old or {}).get("password_credential_id")
    if rotate_password or pw_id is None:
        pw = G.new_fix_password()
        pw_id = (await _rotate(conn, cp["id"], "fix_password", pw, actor))["credential_id"]
        issued["fix_password"] = pw["value"]
    await _guard(conn.execute(
        "INSERT INTO cpsim.counterparty_fix_sessions (counterparty_id, begin_string, sender_comp_id, target_comp_id, "
        "host, port, heartbt_int, reset_on_logon, username, password_credential_id, created_by) VALUES ($1::uuid, $2, "
        "$3, $4, $5, $6, $7, $8, $9, $10::uuid, $11::uuid)", str(cp["id"]), new["begin_string"],
        new["sender_comp_id"], new["target_comp_id"], new["host"], int(new["port"]), int(new["heartbt_int"] or 30),
        bool(new["reset_on_logon"]), new.get("username") or None, str(pw_id), _uid(actor)))
    return {"before": base, "after": {k: new.get(k) for k in FIX_FIELDS}, "issued": issued}


async def set_behaviour(conn, counterparty_id, fields: dict, actor) -> dict:
    cp = await _require(conn, counterparty_id)
    unknown = set(fields) - set(BEHAVIOUR_FIELDS)
    if unknown:
        raise RegistryError(f"unknown behaviour fields {sorted(unknown)}")
    old = await _close_live(conn, "cpsim.counterparty_behaviour", cp["id"])
    base = {k: (old or {}).get(k) for k in BEHAVIOUR_FIELDS}
    new = {**base, **fields}
    if new.get("default_outcome") not in OUTCOMES:
        raise RegistryError(f"default outcome must be one of {', '.join(OUTCOMES)}")
    await _guard(conn.execute(
        "INSERT INTO cpsim.counterparty_behaviour (counterparty_id, default_outcome, reject_reason, partial_pct, "
        "delay_seconds, ach_return_code, ach_return_after_days, webhooks_enabled, webhook_url, created_by) VALUES "
        "($1::uuid, $2, $3, $4::numeric, $5::int, $6, $7::int, $8, $9, $10::uuid)", str(cp["id"]), new["default_outcome"],
        new.get("reject_reason") or None, None if new.get("partial_pct") is None else str(new["partial_pct"]),
        new.get("delay_seconds"), new.get("ach_return_code") or None, new.get("ach_return_after_days"),
        bool(new.get("webhooks_enabled")), new.get("webhook_url") or None, _uid(actor)))
    return {"before": _jsonable(base), "after": _jsonable({k: new.get(k) for k in BEHAVIOUR_FIELDS})}


def _jsonable(d: dict) -> dict:
    return {k: (str(v) if v is not None and not isinstance(v, (bool, int, str)) else v) for k, v in d.items()}


async def regenerate_bank(conn, counterparty_id, actor) -> dict:
    cp = await _require(conn, counterparty_id)
    typ = await institution_type_or_fail(conn, cp["institution_type"])
    kinds = [k for g, k in BANK_GROUPS.items() if g in typ["field_groups"]]
    if not kinds:
        raise RegistryError(f"{typ['label']} has no bank details")
    for kind in kinds:
        await _close_live(conn, "cpsim.counterparty_bank_details", cp["id"], " AND kind = $2", kind)
        await _insert_bank(conn, cp["id"], G.fake_bank_details(kind, cp["name"]), actor)
    return {"kinds": kinds}


async def advance_clock(conn, counterparty_id, actor) -> dict:
    cp = await _require(conn, counterparty_id)
    old = await _close_live(conn, "cpsim.counterparty_clock", cp["id"])
    start = old["business_date"] if old else today()
    nxt = next_business_day(start + timedelta(days=1))
    assert is_business_day(nxt) and nxt > start
    await _guard(conn.execute(
        "INSERT INTO cpsim.counterparty_clock (counterparty_id, business_date, reason, set_by) VALUES ($1::uuid, $2, "
        "'advance', $3::uuid)", str(cp["id"]), nxt, _uid(actor)))
    return {"from": start.isoformat(), "to": nxt.isoformat()}


# ── the sub-app's auth + log ────────────────────────────────────────────────────────────────────────────────────
async def authenticate(conn, cp: dict, presented: str | None) -> str:
    """The auth outcome for ``presented`` (the bearer value) against counterparty ``cp``: 'ok' or a refusal reason.
    Constant-time per stored credential; every credential the counterparty ever had is compared, so a rotated or
    revoked key is recognised as such ('revoked') rather than merely unknown."""
    if presented is None:
        return "missing"
    if presented == "":
        return "malformed"
    if not G.is_simulator_secret(presented):
        return "not_simulator_key"
    outcome = "unknown_key"
    for r in await conn.fetch("SELECT key_hash, salt, rotated_at, revoked_at FROM cpsim.counterparty_credentials "
                              "WHERE counterparty_id = $1::uuid AND kind = 'api_key'", str(cp["id"])):
        if G.matches(r["salt"], r["key_hash"], presented):
            outcome = "ok" if (r["rotated_at"] is None and r["revoked_at"] is None) else "revoked"
    if outcome == "ok" and cp["status"] != "active":
        return "disabled"
    return outcome


async def log_request(conn, *, counterparty_id, slug, method, path, status, auth_outcome, shape_valid=None,
                      error_count=0, message_type=None, direction="in", latency_ms=None) -> None:
    safe_path = (path or "/")[:300]
    if "cpsim_" in safe_path:
        safe_path = "[redacted path]"
    mt = message_type if (isinstance(message_type, str) and "cpsim_" not in message_type) else None
    await conn.execute(
        "INSERT INTO cpsim.request_log (counterparty_id, slug, method, path, status, auth_outcome, shape_valid, "
        "error_count, message_type, direction, latency_ms) VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)",
        str(counterparty_id) if counterparty_id else None, slug, method if method in (
            "GET", "POST", "PUT", "PATCH", "DELETE") else "GET", safe_path, int(status), auth_outcome, shape_valid,
        int(error_count), mt[:80] if mt else None, direction if direction in ("in", "out") else "in",
        None if latency_ms is None else max(0, int(latency_ms)))


async def may_connect(conn, org_id, household_id=None) -> bool:
    """Principle 3 (docs/cpsim01_part1.sql cpsim.may_connect): the setting on, then a test org freely or a real org's
    demo household only. cpsim02 calls it before pointing an org's connection at a simulator."""
    return bool(await conn.fetchval("SELECT cpsim.may_connect($1::uuid, $2::uuid)", str(org_id) if org_id else None,
                                    str(household_id) if household_id else None))
