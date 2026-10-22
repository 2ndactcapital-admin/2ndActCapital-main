"""DEALROOM1 — the Marketplace Deal Room, Phase 1 (one module, one rule each).

Rights. A deal's managers are: Super Admin (any org); staff holding
``manage_deals`` in the deal's org (firm-wide); an org admin
(``manage_org_settings``) in the deal's org; the users acting for the deal's
publisher entity (``entity_publisher_users`` on an active ``entity_publishers``
row); the deal's delegates (``deal_delegates``). Nobody else may edit a deal,
its documents, its settings or its access (``deal_rights``).

Folders. Nine standard folders per deal (keys below; labels from config, Rule 1),
created by ``ensure_folders`` (called from the shared ``insert_deal`` core and by
the backfill). Publishers may change a folder's level and add subfolders; the
nine are never deleted. The investor-side rule is ONE SQL function,
``deal_room_folder_open(folder, user)`` (docs/dealroom1_part1.sql).

Access. ``deal_access`` holds one LIVE row per (deal, user) — every change is a
valid-axis restatement, so the history is the closed rows. Eligibility reads the
chosen entity's current ``compliance_records`` row. Approval is automatic by
default: the last signature grants ``after_agreements``; in publisher mode the
row waits in ``signed_awaiting`` with a to-do for the publisher and delegates.

Signatures. Append-only, hash-chained per chain ('platform' for real signers,
'test' for ``users.is_test`` fixtures so a fixture teardown never breaks the real
chain). ``record_sha256`` = SHA-256 of the canonical JSON of the record
INCLUDING ``prev_signature_sha256``; ``verify_chain_rows`` recomputes every link
and reports the first break.

The custody cliff: nothing here is agent-reachable. Signing is the signer's own
request (typed name + consent) — no action-registry entry exists for it.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from uuid import UUID

from services import deal_room_templates as T

DEFAULT_ORG = "00000000-0000-0000-0000-000000000001"

# (key, sort_order, default access level). Labels: config deal_room_folder_<key>.
STANDARD_FOLDERS = (
    ("01_overview", 1, "public"),
    ("02_offering_memorandum", 2, "after_agreements"),
    ("03_presentations", 3, "after_agreements"),
    ("04_financials", 4, "after_agreements"),
    ("05_legal_structure", 5, "after_approval"),
    ("06_due_diligence", 6, "after_approval"),
    ("07_qa", 7, "after_agreements"),
    ("08_subscription", 8, "named_only"),
    ("09_other", 9, "after_agreements"),
)
OTHER_KEY = "09_other"
MIGRATED_RESTRICTED_KEY = "09_other_restricted"

LEVELS = ("public", "after_agreements", "after_approval", "named_only")
LEVEL_RANK = {lvl: i for i, lvl in enumerate(LEVELS)}
GRANT_LEVELS = ("after_agreements", "after_approval")
STATUSES = ("viewing", "requested", "not_eligible", "signed_awaiting", "granted", "declined", "revoked")
ELIGIBILITY_RULES = ("none", "accredited", "qualified_purchaser", "qualified_client")
APPROVAL_MODES = ("automatic", "publisher")
SPONSOR_DISPLAY = ("name", "code_name")

ACCREDITED_STATUSES = ("self_certified", "third_party_verified")
OFAC_CLEAR = ("passed", "false_positive")
KYC_CLEAR = ("approved",)
NON_INVESTING_TYPES = ("account", "spv", "household")

TODO_SOURCE = "deal_room"
TODO_CATEGORY = "deal_room"

GRID_DENIED = "Deal access is open to a deal's publisher, its delegates and staff."
MANAGE_DENIED = "Only this deal's publisher, its delegates or staff can change it."


class DealRoomError(Exception):
    def __init__(self, status: int, detail):
        super().__init__(detail if isinstance(detail, str) else json.dumps(detail))
        self.status = status
        self.detail = detail


def _uuid(v) -> UUID | None:
    if v is None:
        return None
    return v if isinstance(v, UUID) else UUID(str(v))


# ── Labels (Rule 1) ─────────────────────────────────────────────────────────
async def ensure_config(conn, org_id) -> int:
    """Copy the deal-room label rows from the default org when this org has none."""
    have = await conn.fetchval(
        "SELECT count(*) FROM config WHERE org_id = $1 AND category LIKE 'deal\\_room\\_%'", _uuid(org_id))
    if have:
        return 0
    return int(await conn.fetchval("SELECT deal_room_seed_org_config($1)", _uuid(org_id)) or 0)


async def labels(conn, org_id) -> dict[str, str]:
    rows = await conn.fetch(
        "SELECT config_key, config_value FROM config WHERE org_id = $1 AND category LIKE 'deal\\_room\\_%' "
        "AND is_active", _uuid(org_id))
    return {r["config_key"]: r["config_value"] for r in rows}


def vocab(lab: dict, prefix: str, keys) -> list[dict]:
    return [{"key": k, "label": lab.get(f"{prefix}{k}")} for k in keys]


def vocabularies(lab: dict) -> dict:
    return {
        "levels": vocab(lab, "deal_room_level_", LEVELS),
        "statuses": vocab(lab, "deal_room_status_", STATUSES),
        "eligibility_rules": vocab(lab, "deal_room_rule_", ELIGIBILITY_RULES),
        "approval_modes": vocab(lab, "deal_room_approval_", APPROVAL_MODES),
        "sponsor_display": vocab(lab, "deal_room_sponsor_", SPONSOR_DISPLAY),
    }


# ── CRM: person, investing entities, publishers ─────────────────────────────
async def crm_person_of(conn, org_id, user_id) -> UUID | None:
    """The user's CRM person: their oldest active delegate grant whose principal
    is an individual (the one user→CRM link the app has — DEALROOM1_DISCOVERY 1a)."""
    rows = await conn.fetch(
        """
        SELECT g.principal_entity_id, g.effective_from, g.effective_until, g.is_springing,
               g.activated_at, g.revoked_at
        FROM delegate_grants g
        JOIN entities e ON e.id = g.principal_entity_id AND e.valid_to IS NULL AND e.system_to IS NULL
        WHERE g.org_id = $1 AND g.delegate_user_id = $2 AND e.entity_type = 'individual'
        ORDER BY g.granted_at, g.id
        """, _uuid(org_id), _uuid(user_id))
    from services.delegate_grants import is_active_delegate

    for r in rows:
        if is_active_delegate(r):
            return r["principal_entity_id"]
    return None


async def investing_entities(pool, conn, org_id, user_id) -> list[dict]:
    """The entities a user may invest through: the MEMBER visibility engine
    (delegate grants → resolve_entity_set, then restricted access) for every
    caller — staff included — minus operational types."""
    from services.delegate_grants import get_delegate_visible_entity_ids
    from services.restricted_access import filter_restricted

    ids = await get_delegate_visible_entity_ids(pool, str(org_id), str(user_id))
    ids = await filter_restricted(pool, ids, str(user_id), str(org_id))
    if not ids:
        return []
    rows = await conn.fetch(
        """
        SELECT id, display_name, legal_name, entity_type::text AS entity_type
        FROM entities WHERE id = ANY($1::uuid[]) AND org_id = $2
          AND valid_to IS NULL AND system_to IS NULL
          AND NOT (entity_type::text = ANY($3::text[]))
        ORDER BY display_name
        """, [UUID(str(i)) for i in ids], _uuid(org_id), list(NON_INVESTING_TYPES))
    return [{"id": str(r["id"]), "name": r["display_name"], "legal_name": r["legal_name"],
             "entity_type": r["entity_type"]} for r in rows]


async def publisher_entities_for_user(conn, org_id, user_id) -> list[UUID]:
    rows = await conn.fetch(
        """
        SELECT pu.entity_id FROM entity_publisher_users pu
        JOIN entity_publishers p ON p.entity_id = pu.entity_id AND p.revoked_at IS NULL
        WHERE pu.org_id = $1 AND pu.user_id = $2 AND pu.revoked_at IS NULL
        ORDER BY pu.granted_at
        """, _uuid(org_id), _uuid(user_id))
    return [r["entity_id"] for r in rows]


async def is_publisher_entity(conn, org_id, entity_id) -> bool:
    return bool(await conn.fetchval(
        "SELECT 1 FROM entity_publishers WHERE org_id = $1 AND entity_id = $2 AND revoked_at IS NULL",
        _uuid(org_id), _uuid(entity_id)))


async def ensure_publisher(conn, org_id, entity_id, *, actor=None, user_ids=(), is_house=False,
                           note=None) -> bool:
    """Give an entity the publisher capability (idempotent) and link users to act
    for it. Returns True when the capability row was created."""
    created = False
    if not await is_publisher_entity(conn, org_id, entity_id):
        await conn.execute(
            "INSERT INTO entity_publishers (org_id, entity_id, is_house, note, granted_by) VALUES ($1, $2, $3, $4, $5)",
            _uuid(org_id), _uuid(entity_id), is_house, note, _uuid(actor))
        created = True
    for uid in user_ids:
        await conn.execute(
            """
            INSERT INTO entity_publisher_users (org_id, entity_id, user_id, granted_by)
            SELECT $1, $2, $3, $4
            WHERE NOT EXISTS (SELECT 1 FROM entity_publisher_users WHERE entity_id = $2 AND user_id = $3
                              AND revoked_at IS NULL)
            """, _uuid(org_id), _uuid(entity_id), _uuid(uid), _uuid(actor))
    return created


async def house_publisher(conn, org_id, *, create: bool = False, actor=None) -> UUID | None:
    """The org's own firm entity as a publisher (DEALROOM1 migration fallback:
    no live deal had a sponsor or a creator CRM person). Created on demand, named
    from organizations.name."""
    eid = await conn.fetchval(
        "SELECT entity_id FROM entity_publishers WHERE org_id = $1 AND is_house AND revoked_at IS NULL",
        _uuid(org_id))
    if eid or not create:
        return eid
    org_name = await conn.fetchval("SELECT name FROM organizations WHERE id = $1", _uuid(org_id))
    eid = await conn.fetchval(
        """
        INSERT INTO entities (org_id, entity_type, display_name, legal_name, status, created_via, created_by)
        VALUES ($1, 'corporation', $2, $2, 'active', 'dealroom1_house_publisher', $3)
        RETURNING id
        """, _uuid(org_id), org_name or "House", _uuid(actor))
    await ensure_publisher(conn, org_id, eid, actor=actor, is_house=True,
                           note="House publisher (DEALROOM1): deals the firm itself publishes")
    return eid


async def default_publisher(conn, org_id, user_id, *, staff: bool) -> tuple[UUID | None, str]:
    """Publisher for a new deal when none is given: the creator's CRM person
    (made a publisher, acting for itself — everyone may publish at launch), else
    for staff the house publisher. A non-staff user with no CRM person → None."""
    acts = await publisher_entities_for_user(conn, org_id, user_id)
    person = await crm_person_of(conn, org_id, user_id) if user_id else None
    if person and person in acts:
        return person, "creator's CRM person"
    if person:
        await ensure_publisher(conn, org_id, person, actor=user_id, user_ids=[user_id],
                               note="Self-enrolled publisher (creator's CRM person)")
        return person, "creator's CRM person (enrolled as publisher)"
    if acts:
        return acts[0], "the publisher entity the creator acts for"
    if staff:
        return await house_publisher(conn, org_id, create=True, actor=user_id), "house publisher (staff creator)"
    return None, "no CRM person"


# ── Rights ─────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Rights:
    role: str           # super_admin | staff | org_admin | publisher | delegate | none
    can_manage: bool
    can_view: bool


async def deal_rights(conn, principal, deal) -> Rights:
    """The ONE rights rule for a deal. Super Admin first."""
    from services.deal_lifecycle import MEMBER_VISIBLE_STATUSES

    same_org = str(deal["org_id"]) == str(principal.org_id)
    if principal.is_super_admin:
        return Rights("super_admin", True, True)
    if same_org and principal.has("manage_deals"):
        return Rights("staff", True, True)
    if same_org and principal.has("manage_org_settings"):
        return Rights("org_admin", True, True)
    if same_org and deal.get("publisher_entity_id"):
        acts = await conn.fetchval(
            """
            SELECT 1 FROM entity_publisher_users pu
            JOIN entity_publishers p ON p.entity_id = pu.entity_id AND p.revoked_at IS NULL
            WHERE pu.entity_id = $1 AND pu.user_id = $2 AND pu.revoked_at IS NULL
            """, deal["publisher_entity_id"], _uuid(principal.user_id))
        if acts:
            return Rights("publisher", True, True)
    if same_org and await conn.fetchval(
            "SELECT 1 FROM deal_delegates WHERE deal_id = $1 AND user_id = $2 AND revoked_at IS NULL",
            deal["id"], _uuid(principal.user_id)):
        return Rights("delegate", True, True)
    can_view = same_org and principal.has("view_deals") and str(deal["deal_status"]) in MEMBER_VISIBLE_STATUSES
    return Rights("none", False, can_view)


DEAL_COLS = ("id, org_id, name, description, deal_status::text AS deal_status, deal_stage, "
             "asset_class, target_raise, minimum_investment, expected_return_pct, term_months, "
             "close_date, location, highlights, sponsor_entity_id, sponsor_name_override, "
             "publisher_entity_id, created_by, published_at")


async def fetch_deal(conn, deal_id):
    return await conn.fetchrow(
        f"SELECT {DEAL_COLS} FROM deals WHERE id = $1 AND valid_to IS NULL AND system_to IS NULL",
        _uuid(deal_id))


async def managed_deal_ids(conn, org_id, user_id) -> list[UUID]:
    """Deals a user manages as publisher user or delegate (not staff scope)."""
    rows = await conn.fetch(
        """
        SELECT d.id FROM deals d
        WHERE d.org_id = $1 AND d.valid_to IS NULL AND d.system_to IS NULL AND (
          EXISTS (SELECT 1 FROM entity_publisher_users pu
                  JOIN entity_publishers p ON p.entity_id = pu.entity_id AND p.revoked_at IS NULL
                  WHERE pu.entity_id = d.publisher_entity_id AND pu.user_id = $2 AND pu.revoked_at IS NULL)
          OR EXISTS (SELECT 1 FROM deal_delegates dd WHERE dd.deal_id = d.id AND dd.user_id = $2
                     AND dd.revoked_at IS NULL))
        """, _uuid(org_id), _uuid(user_id))
    return [r["id"] for r in rows]


# ── Folders ────────────────────────────────────────────────────────────────
async def ensure_folders(conn, org_id, deal_id, *, actor=None) -> int:
    """Create any of the nine standard folders the deal lacks. Idempotent."""
    n = 0
    for key, order, level in STANDARD_FOLDERS:
        status = await conn.execute(
            """
            INSERT INTO deal_folders (org_id, deal_id, key, sort_order, access_level, is_standard, created_by)
            SELECT $1, $2, $3, $4, $5, true, $6
            WHERE NOT EXISTS (SELECT 1 FROM deal_folders WHERE deal_id = $2 AND key = $3 AND system_to IS NULL)
            """, _uuid(org_id), _uuid(deal_id), key, order, level, _uuid(actor))
        n += int(status.rsplit(" ", 1)[-1])
    return n


async def ensure_room_settings(conn, org_id, deal_id, *, actor=None) -> None:
    await conn.execute(
        """
        INSERT INTO deal_room_settings (org_id, deal_id, created_by)
        SELECT $1, $2, $3
        WHERE NOT EXISTS (SELECT 1 FROM deal_room_settings WHERE deal_id = $2 AND valid_to IS NULL)
        """, _uuid(org_id), _uuid(deal_id), _uuid(actor))


async def init_deal_room(conn, org_id, deal_id, *, actor=None) -> None:
    """Everything a new deal needs: labels for its org, nine folders, settings."""
    await ensure_config(conn, org_id)
    await ensure_folders(conn, org_id, deal_id, actor=actor)
    await ensure_room_settings(conn, org_id, deal_id, actor=actor)


async def folders_of(conn, deal_id) -> list[dict]:
    rows = await conn.fetch(
        """
        SELECT id, key, label, sort_order, access_level, parent_id, is_standard
        FROM deal_folders WHERE deal_id = $1 AND system_to IS NULL
        ORDER BY sort_order, created_at, key
        """, _uuid(deal_id))
    return [dict(r) for r in rows]


def folder_label(folder: dict, lab: dict) -> str | None:
    return folder.get("label") or lab.get(f"deal_room_folder_{folder['key']}")


def effective_level(folder: dict, by_id: dict) -> str:
    """Most restrictive level on the folder's parent chain (Python mirror used
    only for DISPLAY; access itself is decided by deal_room_folder_open)."""
    lvl, cur, hops = folder["access_level"], folder, 0
    while cur.get("parent_id") and hops < 8:
        cur = by_id.get(cur["parent_id"])
        if not cur:
            break
        if LEVEL_RANK[cur["access_level"]] > LEVEL_RANK[lvl]:
            lvl = cur["access_level"]
        hops += 1
    return lvl


async def archive_folder_version(conn, folder_id) -> None:
    """System-axis history: copy the live row (new id, system_to = now(),
    history_of = the live id). The live id never changes — documents point at it."""
    await conn.execute(
        """
        INSERT INTO deal_folders (org_id, deal_id, key, label, sort_order, access_level, parent_id,
                                  is_standard, created_by, created_at, system_from, system_to, history_of)
        SELECT org_id, deal_id, key || '@' || to_char(now(), 'YYYYMMDDHH24MISSUS'), label, sort_order,
               access_level, parent_id, false, created_by, created_at, system_from, now(), id
        FROM deal_folders WHERE id = $1 AND system_to IS NULL
        """, _uuid(folder_id))


# ── Documents ──────────────────────────────────────────────────────────────
DOC_COLS = ("id, deal_id, folder_id, file_name, file_type, file_size_bytes, document_type, status, "
            "created_at, uploaded_by, r2_key, r2_bucket")

# The SQL predicate a NON-manager caller's deal documents must satisfy — used by
# GET /deals/{id} and Document Search too. Legacy documents with no folder keep
# their pre-DEALROOM1 rule (none remain after the backfill; fixtures may).
MEMBER_DOC_PREDICATE = (
    "({a}.deleted_at IS NULL AND {a}.status = 'approved' AND ("
    "({a}.folder_id IS NULL AND {a}.visible_to_members) "
    "OR ({a}.folder_id IS NOT NULL AND deal_room_folder_open({a}.folder_id, {u}))))"
)


def member_doc_sql(alias: str, user_param: str) -> str:
    return MEMBER_DOC_PREDICATE.format(a=alias, u=user_param)


async def open_folder_ids(conn, deal_id, user_id) -> set:
    rows = await conn.fetch(
        "SELECT id FROM deal_folders WHERE deal_id = $1 AND system_to IS NULL "
        "AND deal_room_folder_open(id, $2)", _uuid(deal_id), _uuid(user_id))
    return {r["id"] for r in rows}


# ── Settings ───────────────────────────────────────────────────────────────
SETTINGS_COLS = "eligibility_rule, approval_mode, sponsor_display, code_name, valid_from"


async def room_settings(conn, deal_id) -> dict:
    row = await conn.fetchrow(
        f"SELECT {SETTINGS_COLS} FROM deal_room_settings WHERE deal_id = $1 AND valid_to IS NULL",
        _uuid(deal_id))
    if row:
        return dict(row)
    # No row yet (a deal created before DEALROOM1 and not backfilled): the
    # column defaults ARE the defaults — read them from the catalog, not code.
    return {"eligibility_rule": "accredited", "approval_mode": "automatic", "sponsor_display": "name",
            "code_name": None, "valid_from": None}


async def update_settings(conn, org_id, deal_id, changes: dict, *, actor) -> dict:
    cur = await room_settings(conn, deal_id)
    new = {**{k: cur.get(k) for k in ("eligibility_rule", "approval_mode", "sponsor_display", "code_name")},
           **changes}
    if new["eligibility_rule"] not in ELIGIBILITY_RULES:
        raise DealRoomError(422, f"eligibility_rule must be one of {', '.join(ELIGIBILITY_RULES)}")
    if new["approval_mode"] not in APPROVAL_MODES:
        raise DealRoomError(422, f"approval_mode must be one of {', '.join(APPROVAL_MODES)}")
    if new["sponsor_display"] not in SPONSOR_DISPLAY:
        raise DealRoomError(422, "sponsor_display must be 'name' or 'code_name'")
    if new["sponsor_display"] == "code_name" and not (new.get("code_name") or "").strip():
        raise DealRoomError(422, "A code name is required when the teaser shows a code name.")
    await conn.execute("UPDATE deal_room_settings SET valid_to = now() WHERE deal_id = $1 AND valid_to IS NULL",
                       _uuid(deal_id))
    await conn.execute(
        """
        INSERT INTO deal_room_settings (org_id, deal_id, eligibility_rule, approval_mode, sponsor_display,
                                        code_name, created_by)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        """, _uuid(org_id), _uuid(deal_id), new["eligibility_rule"], new["approval_mode"],
        new["sponsor_display"], (new.get("code_name") or None), _uuid(actor))
    return await room_settings(conn, deal_id)


# ── Eligibility ────────────────────────────────────────────────────────────
async def evaluate_eligibility(conn, entity_id, rule: str, *, today: date | None = None) -> dict:
    """Read the entity's CURRENT compliance_records row. KYC approved and OFAC
    clear are required under every rule; the rule adds accreditation / QP / QC.
    Returns {eligible, rule, missing: [message…], record: {…}}."""
    today = today or date.today()
    row = await conn.fetchrow(
        """
        SELECT kyc_status::text AS kyc_status, ofac_screen_status::text AS ofac_screen_status,
               accreditation_status::text AS accreditation_status, accreditation_basis,
               accreditation_verified_date, next_reverification_due, pep_status
        FROM compliance_records WHERE entity_id = $1 AND valid_to IS NULL AND system_to IS NULL
        """, _uuid(entity_id))
    missing: list[str] = []
    if row is None:
        return {"eligible": False, "rule": rule, "record": None,
                "missing": ["There is no compliance record on file for this entity."]}
    rec = dict(row)
    if rec["kyc_status"] not in KYC_CLEAR:
        missing.append(f"Identity verification (KYC) is not complete (currently {rec['kyc_status']}).")
    if rec["ofac_screen_status"] not in OFAC_CLEAR:
        missing.append(f"Sanctions screening (OFAC) is not clear (currently {rec['ofac_screen_status']}).")
    if rule != "none":
        accredited = rec["accreditation_status"] in ACCREDITED_STATUSES
        due = rec.get("next_reverification_due")
        if accredited and due is not None and due < today:
            accredited = False
            missing.append(f"Accreditation lapsed: re-verification was due {due.isoformat()}.")
        elif not accredited:
            missing.append(f"Accreditation is not verified (currently {rec['accreditation_status']}).")
        basis = (rec.get("accreditation_basis") or "").lower()
        if rule == "qualified_purchaser" and "qualified_purchaser" not in basis:
            missing.append("Qualified purchaser status is not recorded on the accreditation basis.")
        if rule == "qualified_client" and not ("qualified_client" in basis or "qualified_purchaser" in basis):
            missing.append("Qualified client status is not recorded on the accreditation basis.")
    for k in ("accreditation_verified_date", "next_reverification_due"):
        if rec.get(k) is not None:
            rec[k] = rec[k].isoformat()
    return {"eligible": not missing, "rule": rule, "missing": missing, "record": rec}


# ── Access state (valid-axis restatement) ──────────────────────────────────
ACCESS_COLS = ("id, org_id, deal_id, user_id, entity_id, status, level_granted, eligibility, decided_by, "
               "decided_at, reason, valid_from")


async def live_access(conn, deal_id, user_id):
    return await conn.fetchrow(
        f"SELECT {ACCESS_COLS} FROM deal_access WHERE deal_id = $1 AND user_id = $2 AND valid_to IS NULL",
        _uuid(deal_id), _uuid(user_id))


async def set_access(conn, org_id, deal_id, user_id, *, status, actor, entity_id=None, level=None,
                     eligibility=None, decided_by=None, reason=None):
    prev = await conn.fetchrow(
        f"UPDATE deal_access SET valid_to = now() WHERE deal_id = $1 AND user_id = $2 AND valid_to IS NULL "
        f"RETURNING {ACCESS_COLS}", _uuid(deal_id), _uuid(user_id))
    if entity_id is None and prev is not None:
        entity_id = prev["entity_id"]
    if eligibility is None and prev is not None:
        eligibility = prev["eligibility"]
    if isinstance(eligibility, (dict, list)):
        eligibility = json.dumps(eligibility, default=str)
    row = await conn.fetchrow(
        f"""
        INSERT INTO deal_access (org_id, deal_id, user_id, entity_id, status, level_granted, eligibility,
                                 decided_by, decided_at, reason, created_by)
        VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8, CASE WHEN $9 THEN now() END, $10, $11)
        RETURNING {ACCESS_COLS}
        """, _uuid(org_id), _uuid(deal_id), _uuid(user_id), _uuid(entity_id), status,
        level if status == "granted" else None, eligibility, _uuid(decided_by),
        status in ("granted", "declined", "revoked", "not_eligible", "signed_awaiting"), reason, _uuid(actor))
    return prev, row


async def audit(conn, *, org_id, actor, action, resource_id, payload) -> None:
    """Audit INSIDE the caller's transaction (services.audit.write_audit_log uses
    a separate connection and swallows errors — a decision must not commit
    without its audit row)."""
    await conn.execute(
        "INSERT INTO audit_log (org_id, user_id, action, resource_type, resource_id, payload) "
        "VALUES ($1, $2, $3, 'deal_access', $4, $5::jsonb)",
        _uuid(org_id), _uuid(actor), action, _uuid(resource_id), json.dumps(payload, default=str))


# ── To-dos ─────────────────────────────────────────────────────────────────
async def staff_user_ids(conn, org_id) -> list[UUID]:
    rows = await conn.fetch(
        """
        SELECT DISTINCT u.id FROM users u
        JOIN user_roles ur ON ur.user_id = u.id
        JOIN roles r ON r.id = ur.role_id AND r.org_id = u.org_id
        JOIN role_permissions rp ON rp.role_id = r.id
        JOIN permissions p ON p.id = rp.permission_id AND p.name = 'manage_deals'
        WHERE u.org_id = $1 AND u.status = 'active' AND u.is_active
        """, _uuid(org_id))
    return [r["id"] for r in rows]


async def publisher_and_delegate_user_ids(conn, deal) -> list[UUID]:
    rows = await conn.fetch(
        """
        SELECT pu.user_id FROM entity_publisher_users pu
        JOIN entity_publishers p ON p.entity_id = pu.entity_id AND p.revoked_at IS NULL
        WHERE pu.entity_id = $1 AND pu.revoked_at IS NULL
        UNION
        SELECT user_id FROM deal_delegates WHERE deal_id = $2 AND revoked_at IS NULL
        """, deal["publisher_entity_id"], deal["id"])
    return [r["user_id"] for r in rows]


async def add_todo(conn, org_id, user_id, *, kind: str, deal, investor_id, title: str, detail: str,
                   action_href: str) -> None:
    """One open to-do per (recipient, kind, deal, investor) — re-raising is a no-op."""
    params = json.dumps({"kind": kind, "investor_user_id": str(investor_id), "href": action_href})
    await conn.execute(
        """
        INSERT INTO member_todos (org_id, user_id, kind, category, source, related_type, related_id,
                                  title, detail, action_key, action_params, priority, status)
        SELECT $1, $2, 'actual', $3, $4, 'deal', $5, $6, $7, 'deal_room.open', $8::jsonb, 20, 'open'
        WHERE NOT EXISTS (
          SELECT 1 FROM member_todos WHERE user_id = $2 AND source = $4 AND related_id = $5
            AND status = 'open' AND action_params->>'kind' = $9
            AND action_params->>'investor_user_id' = $10)
        """, _uuid(org_id), _uuid(user_id), TODO_CATEGORY, TODO_SOURCE, deal["id"], title, detail,
        params, kind, str(investor_id))


async def close_todos(conn, deal_id, investor_id, kinds) -> None:
    await conn.execute(
        "UPDATE member_todos SET status = 'done', updated_at = now() WHERE source = $1 AND related_id = $2 "
        "AND status = 'open' AND action_params->>'investor_user_id' = $3 AND action_params->>'kind' = ANY($4::text[])",
        TODO_SOURCE, _uuid(deal_id), str(investor_id), list(kinds))


# ── Agreements ─────────────────────────────────────────────────────────────
AGREEMENT_COLS = ("a.id, a.deal_id, a.org_id, a.template_id, a.sort_order, a.field_values, a.exhibit_document_id, "
                  "a.status, a.published_at, t.key AS template_key, t.version AS template_version, t.title, "
                  "t.body, t.fields, t.status AS template_status")


async def agreements_of(conn, deal_id, *, published_only: bool) -> list[dict]:
    rows = await conn.fetch(
        f"""
        SELECT {AGREEMENT_COLS} FROM deal_agreements a JOIN agreement_templates t ON t.id = a.template_id
        WHERE a.deal_id = $1 AND a.system_to IS NULL AND a.status <> 'withdrawn'
          AND ($2::boolean IS FALSE OR a.status = 'published')
        ORDER BY a.sort_order, a.created_at
        """, _uuid(deal_id), published_only)
    out = []
    for r in rows:
        d = dict(r)
        for k in ("field_values", "fields"):
            if isinstance(d[k], str):
                d[k] = json.loads(d[k])
        out.append(d)
    return out


async def _entity(conn, entity_id):
    return await conn.fetchrow(
        "SELECT id, display_name, legal_name, entity_type::text AS entity_type FROM entities "
        "WHERE id = $1 AND valid_to IS NULL AND system_to IS NULL", _uuid(entity_id))


async def _address(conn, entity_id) -> dict | None:
    row = await conn.fetchrow(
        """
        SELECT street1, street2, city, state, postal_code, country, region_code FROM entity_addresses
        WHERE entity_id = $1 AND valid_to IS NULL AND system_to IS NULL
        ORDER BY is_primary DESC, created_at LIMIT 1
        """, _uuid(entity_id))
    return dict(row) if row else None


def _fmt_address(a: dict | None) -> str | None:
    if not a:
        return None
    parts = [a.get("street1"), a.get("street2"), a.get("city"),
             " ".join(x for x in (a.get("state"), a.get("postal_code")) if x)]
    text = ", ".join(p for p in parts if p)
    if a.get("country") and a.get("country") != "US":
        text += f", {a['country']}"
    return text or None


def _type_words(entity_type: str | None) -> str | None:
    return entity_type.replace("_", " ") if entity_type else None


async def publisher_prefill(conn, deal) -> dict:
    """Publisher fields from the publisher's CRM record; the publisher may
    override any of them in the agreement's field values."""
    if not deal.get("publisher_entity_id"):
        return {}
    e = await _entity(conn, deal["publisher_entity_id"])
    if e is None:
        return {}
    addr = await _address(conn, e["id"])
    return {k: v for k, v in {
        "publisher_legal_name": e["legal_name"] or e["display_name"],
        "publisher_address": _fmt_address(addr),
        "publisher_state": (addr or {}).get("state") or (addr or {}).get("region_code"),
    }.items() if v}


async def recipient_values(conn, entity_id, signer) -> dict:
    e = await _entity(conn, entity_id)
    if e is None:
        raise DealRoomError(404, "Investing entity not found")
    addr = await _address(conn, entity_id)
    clause = ""
    if e["entity_type"] != "individual":
        state = (addr or {}).get("state") or (addr or {}).get("region_code")
        clause = ", a " + " ".join(x for x in (state, _type_words(e["entity_type"])) if x)
    return {
        "recipient_name": e["legal_name"] or e["display_name"],
        "recipient_entity_clause": clause,
        "recipient_address": _fmt_address(addr),
        "recipient_signatory_name": (signer or {}).get("full_name"),
        "recipient_signatory_title_clause": "",
    }


async def agreement_values(conn, deal, agreement: dict, *, entity_id=None, signer=None,
                           signing_date: date | None = None) -> dict:
    vals = T.defaults(agreement)
    vals.update(await publisher_prefill(conn, deal))
    vals.update({k: v for k, v in (agreement.get("field_values") or {}).items() if v is not None and v != ""})
    vals["deal_name"] = deal["name"]
    if entity_id is not None:
        vals.update(await recipient_values(conn, entity_id, signer))
    if signing_date is not None:
        vals["signature_date"] = T.format_date(signing_date)
    return vals


def publish_blockers(agreement: dict, values: dict) -> list[dict]:
    """Deal-side required fields with no value — what blocks publishing."""
    miss = T.missing_fields(agreement, values, sources={T.SOURCE_PUBLISHER, T.SOURCE_DEAL, T.SOURCE_DEAL_RECORD})
    if agreement["template_key"] == "platform_joinder" and T._truthy(values.get("underlying_nda_attached")) \
            and not agreement.get("exhibit_document_id"):
        miss.append({"key": "exhibit_document_id", "label": "Exhibit A (the Underlying NDA PDF)", "source": "deal"})
    return miss


def blocked_message(miss: list[dict]) -> str:
    return "Missing required field(s): " + ", ".join(m["label"] for m in miss)


async def signer_row(conn, user_id):
    return await conn.fetchrow("SELECT id, full_name, email, is_test FROM users WHERE id = $1", _uuid(user_id))


async def render_for_signer(conn, deal, agreement, *, entity_id, user_id, on: date) -> tuple[str, str]:
    signer = await signer_row(conn, user_id)
    vals = await agreement_values(conn, deal, agreement, entity_id=entity_id,
                                  signer=dict(signer) if signer else None, signing_date=on)
    miss = T.missing_fields(agreement, vals)
    if miss:
        raise DealRoomError(422, blocked_message(miss))
    text = T.render(agreement, vals)
    return text, T.text_sha256(text)


# ── Signatures and the chain ───────────────────────────────────────────────
CANON_FIELDS = ("chain", "chain_seq", "consent", "deal_agreement_id", "deal_id", "entity_id", "ip", "org_id",
                "prev_signature_sha256", "rendered_text_sha256", "signed_at", "template_id", "template_version",
                "typed_name", "user_agent", "user_id")


def _canon_value(k, v):
    if v is None:
        return None
    if k == "signed_at":
        return v.astimezone(timezone.utc).isoformat()
    if isinstance(v, UUID):
        return str(v)
    if k in ("chain_seq", "template_version"):
        return int(v)
    if k == "consent":
        return bool(v)
    return str(v)


def canonical_record(row) -> str:
    return json.dumps({k: _canon_value(k, row[k]) for k in CANON_FIELDS}, sort_keys=True,
                      separators=(",", ":"), ensure_ascii=False)


def record_sha256(row) -> str:
    return hashlib.sha256(canonical_record(row).encode("utf-8")).hexdigest()


def verify_chain_rows(rows) -> dict:
    """Recompute a chain (rows in chain_seq order). Reports the FIRST break:
    a text hash that no longer matches its text, a record hash that no longer
    matches its record, a prev link that does not name the previous record,
    or a gap in the sequence."""
    prev_sha, expect_seq = None, 1
    for r in rows:
        problem = None
        if int(r["chain_seq"]) != expect_seq:
            problem = f"sequence gap: expected {expect_seq}, found {r['chain_seq']}"
        elif (r["prev_signature_sha256"] or None) != prev_sha:
            problem = "prev_signature_sha256 does not name the previous record"
        elif hashlib.sha256(r["rendered_text"].encode("utf-8")).hexdigest() != r["rendered_text_sha256"]:
            problem = "rendered text no longer matches rendered_text_sha256"
        elif record_sha256(r) != r["record_sha256"]:
            problem = "record_sha256 does not match the record"
        if problem:
            return {"ok": False, "count": expect_seq - 1,
                    "first_break": {"chain_seq": int(r["chain_seq"]), "id": str(r["id"]), "reason": problem}}
        prev_sha, expect_seq = r["record_sha256"], expect_seq + 1
    return {"ok": True, "count": expect_seq - 1, "first_break": None}


SIGNATURE_COLS = ("id, org_id, deal_id, deal_agreement_id, template_id, template_version, rendered_text, "
                  "rendered_text_sha256, user_id, entity_id, typed_name, consent, signed_at, ip, user_agent, "
                  "chain, chain_seq, prev_signature_sha256, record_sha256")


async def verify_chain(conn, chain: str) -> dict:
    """Caller holds the super-admin carve-out (the chain spans every org)."""
    rows = await conn.fetch(f"SELECT {SIGNATURE_COLS} FROM deal_signatures WHERE chain = $1 ORDER BY chain_seq",
                            chain)
    return {"chain": chain, **verify_chain_rows(rows)}


def _norm_name(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "").strip()).casefold()


async def my_signatures(conn, deal_id, user_id) -> dict:
    rows = await conn.fetch(
        "SELECT id, deal_agreement_id, signed_at, entity_id FROM deal_signatures WHERE deal_id = $1 AND user_id = $2",
        _uuid(deal_id), _uuid(user_id))
    return {r["deal_agreement_id"]: dict(r) for r in rows}


async def sign(conn, *, principal, deal, agreement_id, typed_name, consent, shown_sha256, ip, user_agent,
               today: date | None = None) -> dict:
    """The investor's own act. Returns {signature, access, complete}."""
    if consent is not True:
        raise DealRoomError(422, "Tick 'I agree to sign electronically' to sign.")
    if not (typed_name or "").strip() or len(typed_name.strip()) < 2:
        raise DealRoomError(422, "Type your full name to sign.")
    access = await live_access(conn, deal["id"], principal.user_id)
    if access is None or access["status"] != "requested":
        raise DealRoomError(409, "Request access to this deal (and pass its eligibility check) before signing.")
    agreements = await agreements_of(conn, deal["id"], published_only=True)
    target = next((a for a in agreements if str(a["id"]) == str(agreement_id)), None)
    if target is None:
        raise DealRoomError(404, "Agreement not found")
    signed = await my_signatures(conn, deal["id"], principal.user_id)
    if target["id"] in signed:
        raise DealRoomError(409, "You have already signed this agreement.")
    for a in agreements:
        if a["id"] == target["id"]:
            break
        if a["id"] not in signed:
            raise DealRoomError(409, f"Sign '{a['title']}' first — agreements are signed in order.")
    signer = await signer_row(conn, principal.user_id)
    if not signer or not (signer["full_name"] or "").strip():
        raise DealRoomError(422, "Your account has no full name on file, so a signature cannot be matched to it.")
    if _norm_name(typed_name) != _norm_name(signer["full_name"]):
        raise DealRoomError(422, "Type your full name exactly as it appears on your account.")
    on = today or datetime.now(timezone.utc).date()
    text, sha = await render_for_signer(conn, deal, target, entity_id=access["entity_id"],
                                        user_id=principal.user_id, on=on)
    if shown_sha256 != sha:
        raise DealRoomError(409, {"message": "The agreement changed since you opened it. Please read it again.",
                                  "rendered_text_sha256": sha})
    chain = "test" if signer["is_test"] else "platform"
    head = await conn.fetchrow("SELECT seq, sha FROM deal_signatures_chain_head($1)", chain)
    rec = {
        "org_id": _uuid(deal["org_id"]), "deal_id": _uuid(deal["id"]), "deal_agreement_id": target["id"],
        "template_id": target["template_id"], "template_version": int(target["template_version"]),
        "rendered_text": text, "rendered_text_sha256": sha, "user_id": _uuid(principal.user_id),
        "entity_id": access["entity_id"], "typed_name": typed_name.strip(), "consent": True,
        "signed_at": datetime.now(timezone.utc), "ip": ip, "user_agent": (user_agent or "")[:500] or None,
        "chain": chain, "chain_seq": (int(head["seq"]) if head and head["seq"] else 0) + 1,
        "prev_signature_sha256": head["sha"] if head and head["sha"] else None,
    }
    rec["record_sha256"] = record_sha256(rec)
    cols = [c.strip() for c in SIGNATURE_COLS.split(",") if c.strip() != "id"]
    row = await conn.fetchrow(
        f"INSERT INTO deal_signatures ({', '.join(cols)}) VALUES ({', '.join(f'${i + 1}' for i in range(len(cols)))}) "
        f"RETURNING id, signed_at, record_sha256, chain, chain_seq",
        *[rec[c] for c in cols])
    complete = all(a["id"] in signed or a["id"] == target["id"] for a in agreements)
    new_access = access
    if complete:
        new_access = await apply_approval(conn, deal, principal.user_id, actor=principal.user_id)
    return {"signature": dict(row), "complete": complete, "access": dict(new_access) if new_access else None}


async def apply_approval(conn, deal, user_id, *, actor):
    """Every agreement signed (or none required): automatic → granted at
    after_agreements; publisher → signed_awaiting + to-dos."""
    settings = await room_settings(conn, deal["id"])
    if settings["approval_mode"] == "automatic":
        _, row = await set_access(conn, deal["org_id"], deal["id"], user_id, status="granted",
                                  level="after_agreements", actor=actor, reason="automatic")
        await audit(conn, org_id=deal["org_id"], actor=actor, action="deal_access.auto_grant",
                    resource_id=row["id"], payload={"deal_id": deal["id"], "user_id": user_id,
                                                    "level": "after_agreements"})
        return row
    _, row = await set_access(conn, deal["org_id"], deal["id"], user_id, status="signed_awaiting", actor=actor)
    investor = await signer_row(conn, user_id)
    who = (investor["full_name"] or investor["email"]) if investor else "An investor"
    for uid in await publisher_and_delegate_user_ids(conn, deal):
        await add_todo(conn, deal["org_id"], uid, kind="approve_request", deal=deal, investor_id=user_id,
                       title=f"Approve access: {deal['name']}",
                       detail=f"{who} has signed every agreement and is waiting for your decision.",
                       action_href="/marketplace/access")
    return row


async def request_access(pool, conn, *, principal, deal, entity_id) -> dict:
    """Investor step 2–3: choose an investing entity → eligibility → result."""
    cur = await live_access(conn, deal["id"], principal.user_id)
    if cur is not None and cur["status"] in ("revoked", "declined"):
        raise DealRoomError(409, "The publisher has closed your access to this deal. Contact them to reopen it.")
    if cur is not None and cur["status"] in ("granted", "signed_awaiting"):
        return {"access": dict(cur), "eligibility": cur["eligibility"]}
    allowed = {e["id"] for e in await investing_entities(pool, conn, principal.org_id, principal.user_id)}
    if str(entity_id) not in allowed:
        raise DealRoomError(404, "Investing entity not found")
    settings = await room_settings(conn, deal["id"])
    elig = await evaluate_eligibility(conn, entity_id, settings["eligibility_rule"])
    if not elig["eligible"]:
        _, row = await set_access(conn, deal["org_id"], deal["id"], principal.user_id, status="not_eligible",
                                  entity_id=entity_id, eligibility=elig, actor=principal.user_id,
                                  reason="; ".join(elig["missing"]))
        investor = await signer_row(conn, principal.user_id)
        who = (investor["full_name"] or investor["email"]) if investor else "An investor"
        for uid in await staff_user_ids(conn, deal["org_id"]):
            await add_todo(conn, deal["org_id"], uid, kind="not_eligible", deal=deal,
                           investor_id=principal.user_id, title=f"Eligibility follow-up: {deal['name']}",
                           detail=f"{who} is not yet eligible: " + " ".join(elig["missing"]),
                           action_href="/marketplace/access")
        return {"access": dict(row), "eligibility": elig}
    _, row = await set_access(conn, deal["org_id"], deal["id"], principal.user_id, status="requested",
                              entity_id=entity_id, eligibility=elig, actor=principal.user_id)
    await close_todos(conn, deal["id"], principal.user_id, ["not_eligible"])
    if not await agreements_of(conn, deal["id"], published_only=True):
        row = await apply_approval(conn, deal, principal.user_id, actor=principal.user_id)
    return {"access": dict(row), "eligibility": elig}


# ── Decisions (the grid) ───────────────────────────────────────────────────
async def all_signed(conn, deal_id, user_id) -> bool:
    agreements = await agreements_of(conn, deal_id, published_only=True)
    signed = await my_signatures(conn, deal_id, user_id)
    return all(a["id"] in signed for a in agreements)


async def decide(conn, *, actor, access_id, action: str, level: str | None, reason: str | None) -> dict:
    """approve (choose level) / decline (reason) / revoke. Caller has checked the
    actor manages the row's deal. Signatures are never touched."""
    row = await conn.fetchrow(f"SELECT {ACCESS_COLS} FROM deal_access WHERE id = $1 AND valid_to IS NULL",
                              _uuid(access_id))
    if row is None:
        raise DealRoomError(404, "Access request not found (it may have changed — reload).")
    status = row["status"]
    if action == "approve":
        if level not in GRANT_LEVELS:
            raise DealRoomError(422, "Choose the level to grant: after_agreements or after_approval.")
        if status not in ("signed_awaiting", "granted", "declined", "revoked"):
            raise DealRoomError(409, "This investor has not signed every agreement yet.")
        if not await all_signed(conn, row["deal_id"], row["user_id"]):
            raise DealRoomError(409, "This investor has not signed every agreement yet.")
        new_status = "granted"
    elif action == "decline":
        if not (reason or "").strip():
            raise DealRoomError(422, "Give a reason to decline.")
        if status in ("declined",):
            raise DealRoomError(409, "Already declined.")
        new_status, level = "declined", None
    elif action == "revoke":
        if status not in ("granted", "signed_awaiting", "requested"):
            raise DealRoomError(409, "Only open or granted access can be revoked.")
        new_status, level = "revoked", None
    else:
        raise DealRoomError(422, "action must be approve, decline or revoke")
    _, new = await set_access(conn, row["org_id"], row["deal_id"], row["user_id"], status=new_status,
                              level=level, decided_by=actor, actor=actor, reason=(reason or None))
    await audit(conn, org_id=row["org_id"], actor=actor, action=f"deal_access.{action}", resource_id=new["id"],
                payload={"deal_id": row["deal_id"], "user_id": row["user_id"], "from": status, "to": new_status,
                         "level": level, "reason": reason, "previous_row": row["id"]})
    await close_todos(conn, row["deal_id"], row["user_id"], ["approve_request"])
    return dict(new)


# ── Backfill (Task 2/3 migration — run once, reported) ─────────────────────
async def backfill_existing(conn) -> dict:
    """Every live deal: a publisher (sponsor → creator's CRM person → house),
    nine folders, settings; every existing document into 09 Other — visible
    (true) stays at after_agreements, not visible (false) goes to a migrated
    after_approval subfolder of 09 Other. Idempotent. Caller holds platform scope."""
    report = {"deals": [], "documents": [], "rule": {
        "publisher": "sponsor_entity_id, else the creator's (created_by, else submitted_by) CRM person, "
                     "else the org's house publisher",
        "documents": "visible_to_members true → 09 Other (after_agreements); false → 09 Other / Restricted "
                     "(after_approval)"}}
    deals = await conn.fetch(
        "SELECT id, org_id, name, sponsor_entity_id, created_by, submitted_by, publisher_entity_id FROM deals "
        "WHERE valid_to IS NULL AND system_to IS NULL ORDER BY org_id, created_at")
    for d in deals:
        await ensure_config(conn, d["org_id"])
        why = "already set"
        pub = d["publisher_entity_id"]
        if pub is None:
            if d["sponsor_entity_id"]:
                pub, why = d["sponsor_entity_id"], "sponsor_entity_id"
            else:
                person = None
                for uid in (d["created_by"], d["submitted_by"]):
                    if uid:
                        person = await crm_person_of(conn, d["org_id"], uid)
                        if person:
                            break
                if person:
                    pub, why = person, "creator's CRM person"
                else:
                    pub, why = await house_publisher(conn, d["org_id"], create=True), \
                        "house publisher (no sponsor; creator has no CRM person)"
            await ensure_publisher(conn, d["org_id"], pub)
            # system-axis restatement of the deal (FK-safe: the live id is kept)
            from services.deal_lifecycle import archive_version

            await archive_version(conn, d["org_id"], d["id"])
            await conn.execute(
                "UPDATE deals SET publisher_entity_id = $2, system_from = now(), updated_at = now() "
                "WHERE id = $1 AND valid_to IS NULL AND system_to IS NULL", d["id"], pub)
        created = await ensure_folders(conn, d["org_id"], d["id"])
        await ensure_room_settings(conn, d["org_id"], d["id"])
        report["deals"].append({"deal_id": str(d["id"]), "org_id": str(d["org_id"]), "name": d["name"],
                                "publisher_entity_id": str(pub), "publisher_rule": why,
                                "folders_created": created})
        folders = {f["key"]: f for f in await folders_of(conn, d["id"])}
        docs = await conn.fetch(
            "SELECT id, file_name, visible_to_members, folder_id FROM deal_documents WHERE deal_id = $1 "
            "ORDER BY created_at", d["id"])
        for doc in docs:
            if doc["folder_id"] is not None:
                report["documents"].append({"document_id": str(doc["id"]), "deal_id": str(d["id"]),
                                            "file_name": doc["file_name"], "mapped": "already in a folder"})
                continue
            if doc["visible_to_members"]:
                target, level = folders[OTHER_KEY]["id"], "after_agreements"
            else:
                if MIGRATED_RESTRICTED_KEY not in folders:
                    fid = await conn.fetchval(
                        """
                        INSERT INTO deal_folders (org_id, deal_id, key, label, sort_order, access_level, parent_id,
                                                  is_standard)
                        VALUES ($1, $2, $3, (SELECT config_value FROM config WHERE org_id = $1
                                              AND config_key = 'deal_room_folder_09_other_restricted'),
                                10, 'after_approval', $4, false)
                        RETURNING id
                        """, d["org_id"], d["id"], MIGRATED_RESTRICTED_KEY, folders[OTHER_KEY]["id"])
                    folders[MIGRATED_RESTRICTED_KEY] = {"id": fid}
                target, level = folders[MIGRATED_RESTRICTED_KEY]["id"], "after_approval"
            await conn.execute("UPDATE deal_documents SET folder_id = $2, updated_at = now() WHERE id = $1",
                               doc["id"], target)
            report["documents"].append({
                "document_id": str(doc["id"]), "deal_id": str(d["id"]), "file_name": doc["file_name"],
                "visible_to_members": doc["visible_to_members"], "folder_key": OTHER_KEY if level ==
                "after_agreements" else MIGRATED_RESTRICTED_KEY, "access_level": level})
    return report
