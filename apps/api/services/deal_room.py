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

DEALROOM2 (Phase 2). Every agreement an investor can sign is an immutable
``agreement_versions`` row: a template agreement's version snapshots the
template's title, body and fields plus the publisher's field values (and the
Exhibit A file hash); an uploaded agreement's version is the PDF's SHA-256.
Publishing creates version 1; changing a published agreement creates the next
version with the publisher's choice — ``keep`` (existing signers stay) or
``resign`` (only the investors whose requirement is no longer met move to
``resign_required``: folders above public close until they sign, and signing
restores ``prior_level``). A signature counts when its version is at or above
the agreement's floor (the latest ``initial``/``resign`` version; for a master
NDA, the publisher's accepted version) and it was signed after the access
row's ``resign_after`` (the bulk "require re-sign"). A publisher's master NDA
is one published agreement that also counts on every covered deal for the SAME
investing entity (``deal_master_ndas``). Signature rows keep their Phase 1
canonical record (``record_version`` 1); Phase 2 rows hash a wider record (2).

DEALROOM3 (Phase 3) adds control and insight — mostly in
``services.deal_room_control``: watermarked / view-only documents, access
expiry and pause on a lapsed eligibility (statuses ``expired`` / ``paused``),
the activity log, Q&A, investor advisors (role ``advisor`` here: can view,
never manage; the folder function evaluates an ACTIVE advisor as their
investor), return-or-destroy notices and the DRAFT 506(b) / 506(c) offering
rules (``deal_room_offering_open`` in SQL; 506(c) in ``evaluate_eligibility``).

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
STATUSES = ("viewing", "requested", "not_eligible", "signed_awaiting", "granted", "declined", "revoked",
            "resign_required", "expired", "paused")
RESIGN_POLICIES = ("keep", "resign")
MASTER_SCOPES = ("all", "selected")
AGREEMENT_KINDS = ("template", "upload")
SIGNABLE_STATUSES = ("requested", "resign_required")
ELIGIBILITY_RULES = ("none", "accredited", "qualified_purchaser", "qualified_client")
APPROVAL_MODES = ("automatic", "publisher")
SPONSOR_DISPLAY = ("name", "code_name")
# DEALROOM3 settings
DOWNLOADS = ("allowed", "view_only")
WATERMARK = ("on", "off")
ACCESS_PERIODS = ("none", "until_date", "days_after_approval")
NOTIFY_EVENTS = ("request", "signature", "first_folder_open", "question")
OFFERING_TYPES = ("not_set", "rule_506b", "rule_506c", "other")
ADVISOR_ROLES = ("lawyer", "accountant", "financial_adviser", "other")
ADVISOR_STATUSES = ("invited", "active", "revoked")
QUESTION_STATUSES = ("open", "answered", "withdrawn", "merged")
ACTIVITY_ACTIONS = ("room_open", "folder_open", "view_page", "download", "question", "answer_read")
THIRD_PARTY_VERIFIED = "third_party_verified"

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
        "resign_policies": vocab(lab, "deal_room_resign_", RESIGN_POLICIES),
        "master_scopes": vocab(lab, "deal_room_master_scope_", MASTER_SCOPES),
        "agreement_kinds": vocab(lab, "deal_room_kind_", AGREEMENT_KINDS),
        "downloads": vocab(lab, "deal_room_downloads_", DOWNLOADS),
        "watermark": vocab(lab, "deal_room_watermark_", WATERMARK),
        "access_periods": vocab(lab, "deal_room_access_period_", ACCESS_PERIODS),
        "notify_events": vocab(lab, "deal_room_notify_", NOTIFY_EVENTS),
        "offering_types": vocab(lab, "deal_room_offering_", OFFERING_TYPES),
        "advisor_roles": vocab(lab, "deal_room_advisor_role_", ADVISOR_ROLES),
        "advisor_statuses": vocab(lab, "deal_room_advisor_status_", ADVISOR_STATUSES),
        "question_statuses": vocab(lab, "deal_room_question_status_", QUESTION_STATUSES),
        "activity_actions": vocab(lab, "deal_room_activity_", ACTIVITY_ACTIONS),
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
    role: str           # super_admin | staff | org_admin | publisher | delegate | advisor | none
    can_manage: bool
    can_view: bool
    advisor_of: UUID | None = None      # DEALROOM3: the investor an advisor acts for
    advisor_status: str | None = None   # invited | active


async def advisor_row(conn, deal_id, user_id):
    """The caller's live advisor row on a deal (invited or active), or None."""
    return await conn.fetchrow(
        "SELECT id, access_user_id, investor_entity_id, status, name, firm, role FROM deal_room_advisors "
        "WHERE deal_id = $1 AND advisor_user_id = $2 AND valid_to IS NULL AND status IN ('invited', 'active')",
        _uuid(deal_id), _uuid(user_id))


async def offering_open(conn, deal_id, user_id) -> bool:
    """The DRAFT 506(b) teaser rule (SQL deal_room_offering_open — one definition)."""
    return bool(await conn.fetchval("SELECT deal_room_offering_open($1, $2)", _uuid(deal_id), _uuid(user_id)))


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
    visible = same_org and str(deal["deal_status"]) in MEMBER_VISIBLE_STATUSES
    # DEALROOM3: an investor's advisor — only when they hold no access row of
    # their own on this deal (then they are simply an investor). Can view;
    # never manage, sign, request, ask or invite.
    if visible and await live_access(conn, deal["id"], principal.user_id) is None:
        adv = await advisor_row(conn, deal["id"], principal.user_id)
        if adv is not None:
            return Rights("advisor", False, await offering_open(conn, deal["id"], principal.user_id),
                          advisor_of=adv["access_user_id"], advisor_status=adv["status"])
    can_view = visible and principal.has("view_deals") and await offering_open(conn, deal["id"], principal.user_id)
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
# DEALROOM3: plus the DRAFT 506(b) offering rule (deal_room_offering_open), so
# a deal hidden from the caller lists none of its documents anywhere.
MEMBER_DOC_PREDICATE = (
    "({a}.deleted_at IS NULL AND {a}.status = 'approved' AND deal_room_offering_open({a}.deal_id, {u}) AND ("
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
SETTINGS_COLS = ("eligibility_rule, approval_mode, sponsor_display, code_name, valid_from, downloads, watermark, "
                 "access_period, access_until, access_days, expiry_notice_days, notify_publisher_on, offering_type")
SETTINGS_FIELDS = ("eligibility_rule", "approval_mode", "sponsor_display", "code_name", "downloads", "watermark",
                   "access_period", "access_until", "access_days", "expiry_notice_days", "notify_publisher_on",
                   "offering_type")


async def room_settings(conn, deal_id) -> dict:
    row = await conn.fetchrow(
        f"SELECT {SETTINGS_COLS} FROM deal_room_settings WHERE deal_id = $1 AND valid_to IS NULL",
        _uuid(deal_id))
    if row:
        out = dict(row)
        out["notify_publisher_on"] = list(out.get("notify_publisher_on") or [])
        return out
    # No row yet (a deal created before DEALROOM1 and not backfilled): today's
    # behaviour — downloads allowed, no watermark (the migration's rule for
    # existing deals), no period, offering not set.
    return {"eligibility_rule": "accredited", "approval_mode": "automatic", "sponsor_display": "name",
            "code_name": None, "valid_from": None, "downloads": "allowed", "watermark": "off",
            "access_period": "none", "access_until": None, "access_days": None, "expiry_notice_days": 7,
            "notify_publisher_on": ["request", "signature", "question"], "offering_type": "not_set"}


async def update_settings(conn, org_id, deal_id, changes: dict, *, actor) -> dict:
    cur = await room_settings(conn, deal_id)
    new = {**{k: cur.get(k) for k in SETTINGS_FIELDS}, **changes}
    if new["eligibility_rule"] not in ELIGIBILITY_RULES:
        raise DealRoomError(422, f"eligibility_rule must be one of {', '.join(ELIGIBILITY_RULES)}")
    if new["approval_mode"] not in APPROVAL_MODES:
        raise DealRoomError(422, f"approval_mode must be one of {', '.join(APPROVAL_MODES)}")
    if new["sponsor_display"] not in SPONSOR_DISPLAY:
        raise DealRoomError(422, "sponsor_display must be 'name' or 'code_name'")
    if new["sponsor_display"] == "code_name" and not (new.get("code_name") or "").strip():
        raise DealRoomError(422, "A code name is required when the teaser shows a code name.")
    validate_phase3_settings(new)
    await conn.execute("UPDATE deal_room_settings SET valid_to = now() WHERE deal_id = $1 AND valid_to IS NULL",
                       _uuid(deal_id))
    await conn.execute(
        """
        INSERT INTO deal_room_settings (org_id, deal_id, eligibility_rule, approval_mode, sponsor_display,
                                        code_name, created_by, downloads, watermark, access_period, access_until,
                                        access_days, expiry_notice_days, notify_publisher_on, offering_type)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14::text[], $15)
        """, _uuid(org_id), _uuid(deal_id), new["eligibility_rule"], new["approval_mode"],
        new["sponsor_display"], (new.get("code_name") or None), _uuid(actor), new["downloads"], new["watermark"],
        new["access_period"], new["access_until"], new["access_days"], int(new["expiry_notice_days"]),
        list(new["notify_publisher_on"]), new["offering_type"])
    return await room_settings(conn, deal_id)


def validate_phase3_settings(new: dict) -> None:
    """DEALROOM3 settings — the same rules the table's CHECKs hold, as messages.
    An access period's unused value is cleared, so switching period kinds works."""
    if new["downloads"] not in DOWNLOADS:
        raise DealRoomError(422, f"downloads must be one of {', '.join(DOWNLOADS)}")
    if new["watermark"] not in WATERMARK:
        raise DealRoomError(422, "watermark must be 'on' or 'off'")
    if new["offering_type"] not in OFFERING_TYPES:
        raise DealRoomError(422, f"offering_type must be one of {', '.join(OFFERING_TYPES)}")
    if new["access_period"] not in ACCESS_PERIODS:
        raise DealRoomError(422, f"access_period must be one of {', '.join(ACCESS_PERIODS)}")
    if isinstance(new.get("access_until"), str):
        new["access_until"] = date.fromisoformat(new["access_until"])
    if new["access_period"] == "none":
        new["access_until"] = new["access_days"] = None
    elif new["access_period"] == "until_date":
        new["access_days"] = None
        if new.get("access_until") is None:
            raise DealRoomError(422, "Choose the date access ends.")
    else:
        new["access_until"] = None
        if not isinstance(new.get("access_days"), int) or not 1 <= new["access_days"] <= 3650:
            raise DealRoomError(422, "Access days after approval must be a whole number from 1 to 3650.")
    try:
        n = int(new.get("expiry_notice_days"))
    except (TypeError, ValueError):
        n = -1
    if not 0 <= n <= 90:
        raise DealRoomError(422, "The expiry notice must be 0 to 90 days before the end date.")
    notify = list(new.get("notify_publisher_on") or [])
    bad = [e for e in notify if e not in NOTIFY_EVENTS]
    if bad:
        raise DealRoomError(422, f"Unknown notification event(s): {', '.join(bad)}")
    new["notify_publisher_on"] = [e for e in NOTIFY_EVENTS if e in notify]


def access_ends_on(settings: dict, today: date) -> date | None:
    """The end date an approval stamps on the access row (access period)."""
    from datetime import timedelta

    if settings.get("access_period") == "until_date":
        return settings.get("access_until")
    if settings.get("access_period") == "days_after_approval":
        return today + timedelta(days=int(settings["access_days"]))
    return None


# ── Eligibility ────────────────────────────────────────────────────────────
async def evaluate_eligibility(conn, entity_id, rule: str, *, today: date | None = None,
                               offering_type: str | None = None) -> dict:
    """Read the entity's CURRENT compliance_records row. KYC approved and OFAC
    clear are required under every rule; the rule adds accreditation / QP / QC.
    DEALROOM3 (DRAFT, pending counsel review): under ``rule_506c`` the
    accreditation must ALSO be third_party_verified — self-certification is
    not enough — whatever the eligibility rule.
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
    if offering_type == "rule_506c":
        due = rec.get("next_reverification_due")
        if rec["accreditation_status"] != THIRD_PARTY_VERIFIED:
            missing.append("This is a Rule 506(c) offering (DRAFT rule, pending counsel review): accreditation must be "
                           f"verified by a third party (currently {rec['accreditation_status']}).")
        elif rule == "none" and due is not None and due < today:
            missing.append(f"Accreditation lapsed: re-verification was due {due.isoformat()}.")
    for k in ("accreditation_verified_date", "next_reverification_due"):
        if rec.get(k) is not None:
            rec[k] = rec[k].isoformat()
    return {"eligible": not missing, "rule": rule, "missing": missing, "record": rec}


# ── Access state (valid-axis restatement) ──────────────────────────────────
ACCESS_COLS = ("id, org_id, deal_id, user_id, entity_id, status, level_granted, eligibility, decided_by, "
               "decided_at, reason, valid_from, prior_level, resign_after, access_ends_on, paused_reason")
_CARRY = object()


async def live_access(conn, deal_id, user_id):
    return await conn.fetchrow(
        f"SELECT {ACCESS_COLS} FROM deal_access WHERE deal_id = $1 AND user_id = $2 AND valid_to IS NULL",
        _uuid(deal_id), _uuid(user_id))


async def set_access(conn, org_id, deal_id, user_id, *, status, actor, entity_id=None, level=None,
                     eligibility=None, decided_by=None, reason=None, prior_level=None, resign_after=_CARRY,
                     ends_on=_CARRY, paused_reason=None):
    """Valid-axis restatement of the live row. ``resign_after`` (the bulk
    "require re-sign" cut-off) and ``access_ends_on`` (DEALROOM3: the access
    period's end) carry forward unless given; ``prior_level`` is kept only on
    a resign_required or paused row, ``paused_reason`` only on a paused one
    (CHECKs)."""
    prev = await conn.fetchrow(
        f"UPDATE deal_access SET valid_to = now() WHERE deal_id = $1 AND user_id = $2 AND valid_to IS NULL "
        f"RETURNING {ACCESS_COLS}", _uuid(deal_id), _uuid(user_id))
    if entity_id is None and prev is not None:
        entity_id = prev["entity_id"]
    if eligibility is None and prev is not None:
        eligibility = prev["eligibility"]
    if isinstance(eligibility, (dict, list)):
        eligibility = json.dumps(eligibility, default=str)
    if resign_after is _CARRY:
        resign_after = prev["resign_after"] if prev is not None else None
    if ends_on is _CARRY:
        ends_on = prev["access_ends_on"] if prev is not None else None
    row = await conn.fetchrow(
        f"""
        INSERT INTO deal_access (org_id, deal_id, user_id, entity_id, status, level_granted, eligibility,
                                 decided_by, decided_at, reason, created_by, prior_level, resign_after,
                                 access_ends_on, paused_reason)
        VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8, CASE WHEN $9 THEN now() END, $10, $11, $12, $13, $14, $15)
        RETURNING {ACCESS_COLS}
        """, _uuid(org_id), _uuid(deal_id), _uuid(user_id), _uuid(entity_id), status,
        level if status == "granted" else None, eligibility, _uuid(decided_by),
        status in ("granted", "declined", "revoked", "not_eligible", "signed_awaiting", "resign_required",
                   "expired", "paused"), reason,
        _uuid(actor), prior_level if status in ("resign_required", "paused") else None, resign_after, ends_on,
        paused_reason if status == "paused" else None)
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
# A deal agreement is the stable "slot" (system-axis: its id never changes);
# what is signed is its current agreement_versions row. A template's title is
# the template's; an upload's is the publisher's.
AGREEMENT_COLS = (
    "a.id, a.deal_id, a.org_id, a.template_id, a.sort_order, a.field_values, a.exhibit_document_id, "
    "a.status, a.published_at, a.kind, a.document_id, a.file_sha256, "
    "t.key AS template_key, t.version AS template_version, COALESCE(t.title, a.title) AS title, "
    "t.body, t.fields, t.status AS template_status, cv.id AS version_id, cv.version AS current_version, "
    "(SELECT max(f.version) FROM agreement_versions f WHERE f.deal_agreement_id = a.id "
    " AND f.resign_policy IN ('initial', 'resign')) AS floor_version")
AGREEMENT_FROM = (
    "FROM deal_agreements a LEFT JOIN agreement_templates t ON t.id = a.template_id "
    "LEFT JOIN LATERAL (SELECT v.id, v.version FROM agreement_versions v WHERE v.deal_agreement_id = a.id "
    "ORDER BY v.version DESC LIMIT 1) cv ON true")


def _agreement_dict(r) -> dict:
    d = dict(r)
    for k in ("field_values", "fields"):
        if isinstance(d.get(k), str):
            d[k] = json.loads(d[k])
    d["fields"] = d.get("fields") or []
    d["field_values"] = d.get("field_values") or {}
    return d


async def agreements_of(conn, deal_id, *, published_only: bool) -> list[dict]:
    rows = await conn.fetch(
        f"""
        SELECT {AGREEMENT_COLS} {AGREEMENT_FROM}
        WHERE a.deal_id = $1 AND a.system_to IS NULL AND a.status <> 'withdrawn'
          AND ($2::boolean IS FALSE OR a.status = 'published')
        ORDER BY a.sort_order, a.created_at
        """, _uuid(deal_id), published_only)
    return [_agreement_dict(r) for r in rows]


async def agreement_by_id(conn, agreement_id) -> dict | None:
    r = await conn.fetchrow(f"SELECT {AGREEMENT_COLS} {AGREEMENT_FROM} WHERE a.id = $1 AND a.system_to IS NULL",
                            _uuid(agreement_id))
    return _agreement_dict(r) if r else None


async def archive_agreement(conn, agreement_id) -> None:
    """System-axis history copy of the live row (status 'withdrawn', history_of
    = the live id); the live id is kept for signatures and versions."""
    await conn.execute(
        """
        INSERT INTO deal_agreements (org_id, deal_id, template_id, sort_order, field_values, exhibit_document_id,
                                     status, created_by, created_at, system_from, system_to, history_of,
                                     kind, title, document_id, file_sha256)
        SELECT org_id, deal_id, template_id, sort_order, field_values, exhibit_document_id, 'withdrawn',
               created_by, created_at, system_from, now(), id, kind, title, document_id, file_sha256
        FROM deal_agreements WHERE id = $1 AND system_to IS NULL
        """, _uuid(agreement_id))


VERSION_COLS = ("id, org_id, deal_id, deal_agreement_id, version, kind, title, template_id, template_key, "
                "template_version, template_status, body, fields, field_values, exhibit_document_id, exhibit_sha256, "
                "document_id, file_name, file_sha256, page_count, resign_policy, created_by, created_at")


async def get_version(conn, version_id) -> dict | None:
    r = await conn.fetchrow(f"SELECT {VERSION_COLS} FROM agreement_versions WHERE id = $1", _uuid(version_id))
    return _agreement_dict(r) if r else None


async def versions_of(conn, agreement_id) -> list[dict]:
    rows = await conn.fetch(f"SELECT {VERSION_COLS} FROM agreement_versions WHERE deal_agreement_id = $1 "
                            "ORDER BY version", _uuid(agreement_id))
    return [_agreement_dict(r) for r in rows]


def version_doc(v: dict) -> dict:
    """A version in the shape the template renderer reads."""
    return {"id": v["deal_agreement_id"], "kind": v["kind"], "title": v["title"], "body": v["body"],
            "fields": v["fields"], "field_values": v["field_values"], "template_key": v["template_key"],
            "exhibit_document_id": v["exhibit_document_id"]}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


async def read_document_bytes(conn, document_id) -> tuple[dict, bytes]:
    """A deal document's bytes from R2 (services.storage — mocked by verify)."""
    import asyncio

    from services import storage

    doc = await conn.fetchrow("SELECT id, file_name, r2_key, r2_bucket FROM deal_documents WHERE id = $1",
                              _uuid(document_id))
    if doc is None:
        raise DealRoomError(404, "Document not found")
    data = await asyncio.to_thread(storage.download_bytes, doc["r2_key"], doc["r2_bucket"])
    return dict(doc), data


def pdf_page_count(data: bytes) -> int:
    """Pages in a PDF; DealRoomError(415) when the bytes are not a readable PDF."""
    import io

    if not data.startswith(b"%PDF-"):
        raise DealRoomError(415, "Only PDF agreements can be uploaded (DOCX and other formats are not supported).")
    try:
        from pypdf import PdfReader

        n = len(PdfReader(io.BytesIO(data)).pages)
    except Exception as exc:  # noqa: BLE001
        raise DealRoomError(415, f"The PDF could not be read: {type(exc).__name__}") from exc
    if n < 1:
        raise DealRoomError(415, "The PDF has no pages.")
    return n


async def create_version(conn, agreement: dict, *, policy: str, actor) -> dict:
    """Snapshot the live agreement as its next immutable version."""
    n = int(agreement.get("current_version") or 0) + 1
    policy = "initial" if n == 1 else policy
    exhibit_sha = None
    if agreement.get("exhibit_document_id"):
        _, data = await read_document_bytes(conn, agreement["exhibit_document_id"])
        exhibit_sha = sha256_bytes(data)
    if agreement["kind"] == "template":
        t = await conn.fetchrow("SELECT id, key, version, title, body, fields, status FROM agreement_templates "
                                "WHERE id = $1", agreement["template_id"])
        row = await conn.fetchrow(
            f"""
            INSERT INTO agreement_versions (org_id, deal_id, deal_agreement_id, version, kind, title, template_id,
                template_key, template_version, template_status, body, fields, field_values, exhibit_document_id,
                exhibit_sha256, resign_policy, created_by)
            VALUES ($1, $2, $3, $4, 'template', $5, $6, $7, $8, $9, $10, $11::jsonb, $12::jsonb, $13, $14, $15, $16)
            RETURNING {VERSION_COLS}
            """, agreement["org_id"], agreement["deal_id"], agreement["id"], n, t["title"], t["id"], t["key"],
            t["version"], t["status"], t["body"],
            t["fields"] if isinstance(t["fields"], str) else json.dumps(t["fields"]),
            json.dumps(agreement.get("field_values") or {}), agreement.get("exhibit_document_id"), exhibit_sha,
            policy, _uuid(actor))
    else:
        doc, data = await read_document_bytes(conn, agreement["document_id"])
        if sha256_bytes(data) != agreement["file_sha256"]:
            raise DealRoomError(409, "The stored file no longer matches the hash recorded at upload.")
        row = await conn.fetchrow(
            f"""
            INSERT INTO agreement_versions (org_id, deal_id, deal_agreement_id, version, kind, title, field_values,
                document_id, file_name, file_sha256, page_count, exhibit_document_id, exhibit_sha256, resign_policy,
                created_by)
            VALUES ($1, $2, $3, $4, 'upload', $5, '{{}}'::jsonb, $6, $7, $8, $9, $10, $11, $12, $13)
            RETURNING {VERSION_COLS}
            """, agreement["org_id"], agreement["deal_id"], agreement["id"], n, agreement["title"],
            agreement["document_id"], doc["file_name"], agreement["file_sha256"], pdf_page_count(data),
            agreement.get("exhibit_document_id"), exhibit_sha, policy, _uuid(actor))
    return _agreement_dict(row)


async def ensure_version(conn, agreement: dict, *, actor) -> dict:
    """A published agreement always has a current version (one published
    before DEALROOM2 gets version 1 on first use). Returns the agreement with
    version_id / current_version set."""
    if agreement.get("version_id") or agreement["status"] != "published":
        return agreement
    v = await create_version(conn, agreement, policy="initial", actor=actor)
    return {**agreement, "version_id": v["id"], "current_version": v["version"],
            "floor_version": agreement.get("floor_version") or 1}


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
    """Deal-side required fields with no value — what blocks publishing. An
    uploaded agreement has no fill-in fields."""
    if agreement.get("kind") == "upload":
        return []
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
    """The text a template agreement's signer reads — from its CURRENT VERSION
    (immutable snapshot) when it has one, else from the live row (a draft)."""
    src = agreement
    if agreement.get("version_id"):
        src = version_doc(await get_version(conn, agreement["version_id"]))
    if src.get("kind") == "upload":
        raise DealRoomError(422, "An uploaded agreement is read as its PDF, not as text.")
    if agreement.get("deal_id") and str(agreement["deal_id"]) != str(deal["id"]):
        # a master NDA signed on a covered deal reads as it was written: for
        # its own (home) deal and publisher, not the covered deal's name
        home = await fetch_deal(conn, agreement["deal_id"])
        deal = dict(home) if home else deal
    signer = await signer_row(conn, user_id)
    vals = await agreement_values(conn, deal, src, entity_id=entity_id,
                                  signer=dict(signer) if signer else None, signing_date=on)
    miss = T.missing_fields(src, vals)
    if miss:
        raise DealRoomError(422, blocked_message(miss))
    text = T.render(src, vals)
    return text, T.text_sha256(text)


def upload_statement(v: dict) -> str:
    """deal_signatures.rendered_text for an uploaded agreement: what was signed,
    named by its file hash (the PDF itself is the document)."""
    lines = [f"Signed document: {v['title']} (version {v['version']})",
             f"File: {v['file_name']}", f"File SHA-256: {v['file_sha256']}",
             f"Pages: {v['page_count']}"]
    if v.get("exhibit_sha256"):
        lines.append(f"Exhibit A SHA-256: {v['exhibit_sha256']}")
    return "\n".join(lines) + "\n"


# ── Master NDA ─────────────────────────────────────────────────────────────
MASTER_COLS = "id, org_id, publisher_entity_id, deal_agreement_id, scope, accepted_min_version, created_at"


async def live_master(conn, publisher_entity_id) -> dict | None:
    if not publisher_entity_id:
        return None
    m = await conn.fetchrow(f"SELECT {MASTER_COLS} FROM deal_master_ndas WHERE publisher_entity_id = $1 "
                            "AND revoked_at IS NULL", _uuid(publisher_entity_id))
    if m is None:
        return None
    out = dict(m)
    out["deal_ids"] = [r["deal_id"] for r in await conn.fetch(
        "SELECT deal_id FROM deal_master_nda_deals WHERE master_id = $1", m["id"])]
    return out


async def master_for_deal(conn, deal) -> dict | None:
    """The publisher's master NDA when it covers this deal (scope all, or the
    deal is selected)."""
    m = await live_master(conn, deal.get("publisher_entity_id"))
    if m is None:
        return None
    if m["scope"] == "all" or _uuid(deal["id"]) in m["deal_ids"]:
        return m
    return None


async def master_deal_ids(conn, master: dict) -> list[UUID]:
    """Every live deal a master NDA covers (its home deal included)."""
    home = await conn.fetchval("SELECT deal_id FROM deal_agreements WHERE id = $1", master["deal_agreement_id"])
    if master["scope"] == "all":
        ids = [r["id"] for r in await conn.fetch(
            "SELECT id FROM deals WHERE publisher_entity_id = $1 AND valid_to IS NULL AND system_to IS NULL",
            master["publisher_entity_id"])]
    else:
        ids = list(master["deal_ids"])
    return list(dict.fromkeys([home, *ids]))


async def set_master(conn, *, org_id, publisher_entity_id, deal_agreement_id, scope, deal_ids,
                     accepted_min_version, actor) -> dict:
    if scope not in MASTER_SCOPES:
        raise DealRoomError(422, "scope must be 'all' or 'selected'")
    a = await agreement_by_id(conn, deal_agreement_id)
    if a is None or a["status"] != "published":
        raise DealRoomError(404, "Published agreement not found")
    home = await fetch_deal(conn, a["deal_id"])
    if home is None or str(home["publisher_entity_id"]) != str(publisher_entity_id):
        raise DealRoomError(422, "The master NDA must be a published agreement on one of this publisher's deals.")
    if accepted_min_version is not None and not (1 <= int(accepted_min_version) <= int(a["current_version"] or 1)):
        raise DealRoomError(422, "accepted_min_version must name an existing version of the agreement.")
    selected = [_uuid(d) for d in (deal_ids or [])]
    if scope == "selected":
        if not selected:
            raise DealRoomError(422, "Choose at least one deal for a selected-deals master NDA.")
        own = {r["id"] for r in await conn.fetch(
            "SELECT id FROM deals WHERE id = ANY($1::uuid[]) AND publisher_entity_id = $2 AND valid_to IS NULL "
            "AND system_to IS NULL", selected, _uuid(publisher_entity_id))}
        if own != set(selected):
            raise DealRoomError(422, "Every selected deal must be one of this publisher's deals.")
    await conn.execute("UPDATE deal_master_ndas SET revoked_at = now(), revoked_by = $2 WHERE publisher_entity_id = $1 "
                       "AND revoked_at IS NULL", _uuid(publisher_entity_id), _uuid(actor))
    mid = await conn.fetchval(
        "INSERT INTO deal_master_ndas (org_id, publisher_entity_id, deal_agreement_id, scope, accepted_min_version, "
        "created_by) VALUES ($1, $2, $3, $4, $5, $6) RETURNING id", _uuid(org_id), _uuid(publisher_entity_id),
        a["id"], scope, accepted_min_version, _uuid(actor))
    if scope == "selected":
        for d in selected:
            await conn.execute("INSERT INTO deal_master_nda_deals (org_id, master_id, deal_id) VALUES ($1, $2, $3)",
                               _uuid(org_id), mid, d)
    return await live_master(conn, publisher_entity_id)


async def revoke_master(conn, publisher_entity_id, *, actor) -> bool:
    res = await conn.execute("UPDATE deal_master_ndas SET revoked_at = now(), revoked_by = $2 "
                             "WHERE publisher_entity_id = $1 AND revoked_at IS NULL",
                             _uuid(publisher_entity_id), _uuid(actor))
    return not res.endswith(" 0")


# ── Requirements and what satisfies them ───────────────────────────────────
async def required_agreements(conn, deal, *, published_only: bool = True) -> list[dict]:
    """The deal's own agreements plus — when the publisher's master NDA covers
    this deal and lives on another deal — the master, first. Each carries
    ``is_master`` (and ``master``)."""
    own = await agreements_of(conn, deal["id"], published_only=published_only)
    for a in own:
        a["is_master"], a["virtual"] = False, False
    m = await master_for_deal(conn, deal)
    if m is None:
        # the master's own home deal requires it whatever its scope
        m = await live_master(conn, deal.get("publisher_entity_id"))
        if m is None or not any(a["id"] == m["deal_agreement_id"] for a in own):
            return own
    hit = next((a for a in own if a["id"] == m["deal_agreement_id"]), None)
    if hit is not None:
        hit["is_master"], hit["master"] = True, m
        return own
    a = await agreement_by_id(conn, m["deal_agreement_id"])
    if a is None or a["status"] != "published":
        return own
    a.update({"is_master": True, "master": m, "virtual": True, "sort_order": 0})
    return [a, *own]


def floor_of(a: dict) -> int:
    """The lowest version a signature may carry and still count."""
    f = int(a.get("floor_version") or 1)
    if a.get("is_master"):
        mv = a["master"].get("accepted_min_version")
        f = max(f, int(mv) if mv is not None else int(a.get("current_version") or 1))
    return f


async def signature_state(conn, deal, reqs: list[dict], user_id, entity_id, resign_after=None) -> dict:
    """{agreement_id: {...}} — whether each requirement is met for this signer.
    Own agreements: the user's signature on this deal. A master NDA: a
    signature by the same investing ENTITY on any deal. Either must be at or
    above the agreement's floor and (when set) after ``resign_after``."""
    own_ids = [a["id"] for a in reqs if not a.get("is_master")]
    master_ids = [a["id"] for a in reqs if a.get("is_master")]
    rows = await conn.fetch(
        """
        SELECT s.id, s.deal_id, s.deal_agreement_id, s.signed_at, s.entity_id, s.user_id,
               COALESCE(v.version, 1) AS version
        FROM deal_signatures s LEFT JOIN agreement_versions v ON v.id = s.agreement_version_id
        WHERE (s.deal_id = $1 AND s.user_id = $2 AND s.deal_agreement_id = ANY($3::uuid[]))
           OR ($5::uuid IS NOT NULL AND s.entity_id = $5 AND s.deal_agreement_id = ANY($4::uuid[]))
        ORDER BY s.signed_at DESC, s.chain_seq DESC
        """, _uuid(deal["id"]), _uuid(user_id), own_ids, master_ids, _uuid(entity_id))
    out = {}
    for a in reqs:
        if a.get("is_master"):
            cands = [r for r in rows if r["deal_agreement_id"] == a["id"] and r["entity_id"] == _uuid(entity_id)]
        else:
            cands = [r for r in rows if r["deal_agreement_id"] == a["id"] and r["deal_id"] == _uuid(deal["id"])
                     and r["user_id"] == _uuid(user_id)]
        fl = floor_of(a)
        valid = [r for r in cands if int(r["version"]) >= fl and (resign_after is None or r["signed_at"] > resign_after)]
        best = valid[0] if valid else None
        latest = cands[0] if cands else None
        out[a["id"]] = {
            "ok": best is not None, "signature_id": best["id"] if best else None,
            "signed_at": best["signed_at"] if best else None, "version": int(best["version"]) if best else None,
            "current_version": a.get("current_version"), "floor": fl,
            "covered": bool(best and a.get("is_master") and best["deal_id"] != _uuid(deal["id"])),
            "stale": latest is not None and best is None,
            "last_version": int(latest["version"]) if latest else None,
            "is_master": bool(a.get("is_master")),
        }
    return out


async def access_state(conn, deal, user_id, access=None) -> tuple[list[dict], dict]:
    access = access if access is not None else await live_access(conn, deal["id"], user_id)
    reqs = await required_agreements(conn, deal)
    state = await signature_state(conn, deal, reqs, user_id, access["entity_id"] if access else None,
                                  access["resign_after"] if access else None)
    return reqs, state


async def all_signed(conn, deal_id, user_id) -> bool:
    deal = await fetch_deal(conn, deal_id)
    if deal is None:
        return False
    _, state = await access_state(conn, dict(deal), user_id)
    return all(s["ok"] for s in state.values())


# ── Signatures and the chain ───────────────────────────────────────────────
# record_version 1 = the Phase 1 canonical record (FIXED — every Phase 1 row's
# record_sha256 was computed over exactly these fields); 2 adds the version.
CANON_FIELDS = ("chain", "chain_seq", "consent", "deal_agreement_id", "deal_id", "entity_id", "ip", "org_id",
                "prev_signature_sha256", "rendered_text_sha256", "signed_at", "template_id", "template_version",
                "typed_name", "user_agent", "user_id")
CANON_FIELDS_V2 = tuple(sorted(CANON_FIELDS + ("agreement_version_id", "document_sha256", "record_version")))


def _canon_value(k, v):
    if v is None:
        return None
    if k == "signed_at":
        return v.astimezone(timezone.utc).isoformat()
    if isinstance(v, UUID):
        return str(v)
    if k in ("chain_seq", "template_version", "record_version"):
        return int(v)
    if k == "consent":
        return bool(v)
    return str(v)


def _record_version(row) -> int:
    try:
        return int(row["record_version"] or 1)
    except (KeyError, IndexError):
        return 1


def canonical_record(row) -> str:
    fields = CANON_FIELDS_V2 if _record_version(row) == 2 else CANON_FIELDS
    return json.dumps({k: _canon_value(k, row[k]) for k in fields}, sort_keys=True,
                      separators=(",", ":"), ensure_ascii=False)


def record_sha256(row) -> str:
    return hashlib.sha256(canonical_record(row).encode("utf-8")).hexdigest()


def verify_chain_rows(rows) -> dict:
    """Recompute a chain (rows in chain_seq order). Reports the FIRST break:
    a text hash that no longer matches its text, a record hash that no longer
    matches its record (Phase 1 or Phase 2 canonical form, by record_version),
    a Phase 2 document hash the text does not account for, a prev link that
    does not name the previous record, or a gap in the sequence."""
    prev_sha, expect_seq = None, 1
    for r in rows:
        problem = None
        if int(r["chain_seq"]) != expect_seq:
            problem = f"sequence gap: expected {expect_seq}, found {r['chain_seq']}"
        elif (r["prev_signature_sha256"] or None) != prev_sha:
            problem = "prev_signature_sha256 does not name the previous record"
        elif hashlib.sha256(r["rendered_text"].encode("utf-8")).hexdigest() != r["rendered_text_sha256"]:
            problem = "rendered text no longer matches rendered_text_sha256"
        elif _record_version(r) == 2 and not (r["document_sha256"] == r["rendered_text_sha256"]
                                              or (r["document_sha256"] or "-") in r["rendered_text"]):
            problem = "document_sha256 is neither the text's hash nor named in the signed statement"
        elif record_sha256(r) != r["record_sha256"]:
            problem = "record_sha256 does not match the record"
        if problem:
            return {"ok": False, "count": expect_seq - 1,
                    "first_break": {"chain_seq": int(r["chain_seq"]), "id": str(r["id"]), "reason": problem}}
        prev_sha, expect_seq = r["record_sha256"], expect_seq + 1
    return {"ok": True, "count": expect_seq - 1, "first_break": None}


SIGNATURE_COLS = ("id, org_id, deal_id, deal_agreement_id, template_id, template_version, rendered_text, "
                  "rendered_text_sha256, user_id, entity_id, typed_name, consent, signed_at, ip, user_agent, "
                  "chain, chain_seq, prev_signature_sha256, record_sha256, agreement_version_id, document_sha256, "
                  "record_version")


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


async def insert_signature(conn, rec: dict) -> dict:
    """Append one record to its chain (the insert trigger re-checks the head)."""
    head = await conn.fetchrow("SELECT seq, sha FROM deal_signatures_chain_head($1)", rec["chain"])
    rec["chain_seq"] = (int(head["seq"]) if head and head["seq"] else 0) + 1
    rec["prev_signature_sha256"] = head["sha"] if head and head["sha"] else None
    rec["record_sha256"] = record_sha256(rec)
    cols = [c.strip() for c in SIGNATURE_COLS.split(",") if c.strip() != "id"]
    row = await conn.fetchrow(
        f"INSERT INTO deal_signatures ({', '.join(cols)}) VALUES ({', '.join(f'${i + 1}' for i in range(len(cols)))}) "
        f"RETURNING id, signed_at, record_sha256, chain, chain_seq, agreement_version_id, document_sha256",
        *[rec.get(c) for c in cols])
    return dict(row)


async def sign(conn, *, principal, deal, agreement_id, typed_name, consent, shown_sha256, ip, user_agent,
               today: date | None = None) -> dict:
    """The investor's own act. ``shown_sha256`` is the hash of what they were
    shown: the rendered text (template) or the PDF file (upload). Returns
    {signature, access, complete}."""
    if consent is not True:
        raise DealRoomError(422, "Tick 'I agree to sign electronically' to sign.")
    if not (typed_name or "").strip() or len(typed_name.strip()) < 2:
        raise DealRoomError(422, "Type your full name to sign.")
    access = await live_access(conn, deal["id"], principal.user_id)
    if access is None or access["status"] not in SIGNABLE_STATUSES:
        raise DealRoomError(409, "Request access to this deal (and pass its eligibility check) before signing.")
    reqs, state = await access_state(conn, deal, principal.user_id, access)
    target = next((a for a in reqs if str(a["id"]) == str(agreement_id)), None)
    if target is None:
        raise DealRoomError(404, "Agreement not found")
    if state[target["id"]]["ok"]:
        raise DealRoomError(409, "Already covered by your master NDA signature." if state[target["id"]]["covered"]
                            else "You have already signed this agreement.")
    for a in reqs:
        if a["id"] == target["id"]:
            break
        if not state[a["id"]]["ok"]:
            raise DealRoomError(409, f"Sign '{a['title']}' first — agreements are signed in order.")
    signer = await signer_row(conn, principal.user_id)
    if not signer or not (signer["full_name"] or "").strip():
        raise DealRoomError(422, "Your account has no full name on file, so a signature cannot be matched to it.")
    if _norm_name(typed_name) != _norm_name(signer["full_name"]):
        raise DealRoomError(422, "Type your full name exactly as it appears on your account.")
    target = await ensure_version(conn, target, actor=principal.user_id)
    v = await get_version(conn, target["version_id"])
    if v["kind"] == "template":
        on = today or datetime.now(timezone.utc).date()
        text, sha = await render_for_signer(conn, deal, target, entity_id=access["entity_id"],
                                            user_id=principal.user_id, on=on)
        doc_sha = sha
        if shown_sha256 != sha:
            raise DealRoomError(409, {"message": "The agreement changed since you opened it. Please read it again.",
                                      "rendered_text_sha256": sha})
    else:
        doc_sha = v["file_sha256"]
        if shown_sha256 != doc_sha:
            raise DealRoomError(409, {"message": "The document changed since you opened it. Please read it again.",
                                      "file_sha256": doc_sha})
        text = upload_statement(v)
        sha = T.text_sha256(text)
    rec = {
        "org_id": _uuid(deal["org_id"]), "deal_id": _uuid(deal["id"]), "deal_agreement_id": target["id"],
        "template_id": v["template_id"], "template_version": v["template_version"],
        "rendered_text": text, "rendered_text_sha256": sha, "user_id": _uuid(principal.user_id),
        "entity_id": access["entity_id"], "typed_name": typed_name.strip(), "consent": True,
        "signed_at": datetime.now(timezone.utc), "ip": ip, "user_agent": (user_agent or "")[:500] or None,
        "chain": "test" if signer["is_test"] else "platform",
        "agreement_version_id": v["id"], "document_sha256": doc_sha, "record_version": 2,
    }
    row = await insert_signature(conn, rec)
    complete = all(state[a["id"]]["ok"] or a["id"] == target["id"] for a in reqs)
    new_access = access
    if complete:
        if access["status"] == "resign_required":
            new_access = await restore_after_resign(conn, deal, access, actor=principal.user_id)
        else:
            new_access = await apply_approval(conn, deal, principal.user_id, actor=principal.user_id)
    return {"signature": row, "complete": complete, "access": dict(new_access) if new_access else None,
            "version": v["version"]}


async def apply_approval(conn, deal, user_id, *, actor):
    """Every agreement signed (or none required): automatic → granted at
    after_agreements; publisher → signed_awaiting + to-dos."""
    settings = await room_settings(conn, deal["id"])
    if settings["approval_mode"] == "automatic":
        _, row = await set_access(conn, deal["org_id"], deal["id"], user_id, status="granted",
                                  level="after_agreements", actor=actor, reason="automatic",
                                  ends_on=access_ends_on(settings, date.today()))
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


async def restore_after_resign(conn, deal, access, *, actor):
    """Re-signing done: the access level the investor had comes back; a row
    that was still awaiting approval goes back through approval."""
    if access["prior_level"]:
        _, row = await set_access(conn, deal["org_id"], deal["id"], access["user_id"], status="granted",
                                  level=access["prior_level"], actor=actor, reason="re-signed")
        await audit(conn, org_id=deal["org_id"], actor=actor, action="deal_access.resign_restore",
                    resource_id=row["id"], payload={"deal_id": deal["id"], "user_id": access["user_id"],
                                                    "level": access["prior_level"]})
    else:
        row = await apply_approval(conn, deal, access["user_id"], actor=actor)
    await close_todos(conn, deal["id"], access["user_id"], ["resign"])
    return row


async def require_resign(conn, deal, user_id, *, actor, reason: str, cutoff: bool) -> dict:
    """granted / signed_awaiting → resign_required, keeping the level to
    restore. ``cutoff`` (the bulk grid action) also makes every signature made
    before now stop counting for this investor on this deal."""
    cur = await live_access(conn, deal["id"], user_id)
    if cur is None or cur["status"] not in ("granted", "signed_awaiting"):
        raise DealRoomError(409, "Only granted or awaiting access can be sent back for re-signing.")
    kw = {"resign_after": datetime.now(timezone.utc)} if cutoff else {}
    _, row = await set_access(conn, deal["org_id"], deal["id"], user_id, status="resign_required", actor=actor,
                              prior_level=cur["level_granted"], decided_by=actor, reason=reason, **kw)
    await audit(conn, org_id=deal["org_id"], actor=actor, action="deal_access.require_resign", resource_id=row["id"],
                payload={"deal_id": deal["id"], "user_id": user_id, "from": cur["status"],
                         "prior_level": cur["level_granted"], "reason": reason, "cutoff": cutoff})
    await close_todos(conn, deal["id"], user_id, ["approve_request"])
    await add_todo(conn, deal["org_id"], user_id, kind="resign", deal=deal, investor_id=user_id,
                   title=f"Re-sign: {deal['name']}",
                   detail=f"{reason} Sign the updated agreement to reopen the data room.",
                   action_href=f"/marketplace/{deal['id']}?tab=documents")
    return dict(row)


async def resign_affected(conn, agreement: dict, *, actor, reason: str) -> list[dict]:
    """After a 'resign' version: every live granted / awaiting investor on the
    agreement's deal (and, for a master NDA, every covered deal) whose
    requirement is no longer met moves to resign_required. Nobody else."""
    deal_ids = [agreement["deal_id"]]
    m = await conn.fetchrow(f"SELECT {MASTER_COLS} FROM deal_master_ndas WHERE deal_agreement_id = $1 "
                            "AND revoked_at IS NULL", agreement["id"])
    if m is not None:
        deal_ids = await master_deal_ids(conn, await live_master(conn, m["publisher_entity_id"]))
    affected = []
    for did in deal_ids:
        deal = await fetch_deal(conn, did)
        if deal is None:
            continue
        deal = dict(deal)
        rows = await conn.fetch(f"SELECT {ACCESS_COLS} FROM deal_access WHERE deal_id = $1 AND valid_to IS NULL "
                                "AND status IN ('granted', 'signed_awaiting')", _uuid(did))
        for acc in rows:
            _, state = await access_state(conn, deal, acc["user_id"], acc)
            if all(s["ok"] for s in state.values()):
                continue
            await require_resign(conn, deal, acc["user_id"], actor=actor, reason=reason, cutoff=False)
            affected.append({"deal_id": did, "user_id": acc["user_id"]})
    return affected


async def new_version(conn, deal, agreement_id, *, actor, policy: str | None, field_values=None,
                      exhibit_document_id=None, new_file: dict | None = None) -> dict:
    """Change a PUBLISHED agreement: the live row moves (system-axis history
    kept), the next immutable version is written, and — when it has
    signatures — the publisher's choice applies: keep or resign."""
    a = await agreement_by_id(conn, agreement_id)
    if a is None or str(a["deal_id"]) != str(deal["id"]) or a["status"] == "withdrawn":
        raise DealRoomError(404, "Agreement not found")
    if a["status"] != "published":
        raise DealRoomError(409, "A draft agreement is edited in place; versions start when it is published.")
    nsig = int(await conn.fetchval("SELECT count(*) FROM deal_signatures WHERE deal_agreement_id = $1", a["id"]))
    if nsig and policy not in RESIGN_POLICIES:
        raise DealRoomError(422, {"message": f"This agreement has {nsig} signature(s). Choose 'keep' (existing "
                                             "signers stay) or 'resign' (they must sign the new version).",
                                  "signatures": nsig, "choices": list(RESIGN_POLICIES)})
    policy = policy if policy in RESIGN_POLICIES else "keep"
    a = await ensure_version(conn, a, actor=actor)
    if a["kind"] == "template":
        if new_file is not None:
            raise DealRoomError(422, "A template agreement takes field values, not a file.")
        merged = {**(a["field_values"] or {}), **(field_values or {})}
        probe = {**a, "field_values": merged, "exhibit_document_id": exhibit_document_id or a["exhibit_document_id"]}
        miss = publish_blockers(probe, await agreement_values(conn, deal, probe))
        if miss:
            raise DealRoomError(422, {"message": blocked_message(miss), "missing": miss})
        await archive_agreement(conn, a["id"])
        await conn.execute("UPDATE deal_agreements SET field_values = $2::jsonb, exhibit_document_id = $3, "
                           "system_from = now() WHERE id = $1", a["id"], json.dumps(merged),
                           probe["exhibit_document_id"])
    else:
        if new_file is None and exhibit_document_id is None:
            raise DealRoomError(422, "Upload the replacement PDF.")
        if field_values:
            raise DealRoomError(422, "An uploaded agreement has no fill-in fields.")
        await archive_agreement(conn, a["id"])
        await conn.execute("UPDATE deal_agreements SET document_id = COALESCE($2, document_id), "
                           "file_sha256 = COALESCE($3, file_sha256), exhibit_document_id = COALESCE($4, "
                           "exhibit_document_id), system_from = now() WHERE id = $1", a["id"],
                           (new_file or {}).get("document_id"), (new_file or {}).get("file_sha256"),
                           exhibit_document_id)
    live = await agreement_by_id(conn, a["id"])
    v = await create_version(conn, live, policy=policy, actor=actor)
    affected = []
    if policy == "resign":
        affected = await resign_affected(conn, live, actor=actor,
                                         reason=f"'{live['title']}' changed (version {v['version']}).")
    await audit(conn, org_id=deal["org_id"], actor=actor, action="deal_room.agreement_version",
                resource_id=a["id"], payload={"version": v["version"], "policy": policy, "signatures": nsig,
                                              "affected": affected})
    return {"version": v, "policy": policy, "signatures": nsig, "affected": affected}


async def request_access(pool, conn, *, principal, deal, entity_id) -> dict:
    """Investor step 2–3: choose an investing entity → eligibility → result."""
    cur = await live_access(conn, deal["id"], principal.user_id)
    if cur is not None and cur["status"] in ("revoked", "declined"):
        raise DealRoomError(409, "The publisher has closed your access to this deal. Contact them to reopen it.")
    if cur is not None and cur["status"] in ("granted", "signed_awaiting", "resign_required", "expired", "paused"):
        return {"access": dict(cur), "eligibility": cur["eligibility"]}
    allowed = {e["id"] for e in await investing_entities(pool, conn, principal.org_id, principal.user_id)}
    if str(entity_id) not in allowed:
        raise DealRoomError(404, "Investing entity not found")
    settings = await room_settings(conn, deal["id"])
    elig = await evaluate_eligibility(conn, entity_id, settings["eligibility_rule"],
                                      offering_type=settings.get("offering_type"))
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
    # nothing to sign — or everything already satisfied (a master NDA the same
    # entity signed on another of this publisher's deals) → approval now
    _, state = await access_state(conn, deal, principal.user_id, row)
    if all(s["ok"] for s in state.values()):
        row = await apply_approval(conn, deal, principal.user_id, actor=principal.user_id)
    return {"access": dict(row), "eligibility": elig}


# ── Decisions (the grid) ───────────────────────────────────────────────────
DECIDE_ACTIONS = ("approve", "decline", "revoke", "require_resign", "extend")


async def decide(conn, *, actor, access_id, action: str, level: str | None, reason: str | None,
                 ends_on: date | None = None, today: date | None = None) -> dict:
    """approve (choose level) / decline (reason) / revoke / require_resign /
    extend (DEALROOM3: a new end date; an expired row reopens at the level it
    had). Caller has checked the actor manages the row's deal. Signatures are
    never touched; revoking or declining also ends the investor's advisors."""
    today = today or date.today()
    row = await conn.fetchrow(f"SELECT {ACCESS_COLS} FROM deal_access WHERE id = $1 AND valid_to IS NULL",
                              _uuid(access_id))
    if row is None:
        raise DealRoomError(404, "Access request not found (it may have changed — reload).")
    status = row["status"]
    if action == "require_resign":
        deal = await fetch_deal(conn, row["deal_id"])
        return await require_resign(conn, dict(deal), row["user_id"], actor=actor, cutoff=True,
                                    reason=(reason or "").strip() or "The publisher asked you to sign again.")
    if action == "extend":
        return await extend_access(conn, row, actor=actor, ends_on=ends_on, reason=reason, today=today)
    kw: dict = {}
    if action == "approve":
        if level not in GRANT_LEVELS:
            raise DealRoomError(422, "Choose the level to grant: after_agreements or after_approval.")
        if status not in ("signed_awaiting", "granted", "declined", "revoked"):
            raise DealRoomError(409, "This investor has not signed every agreement yet.")
        if not await all_signed(conn, row["deal_id"], row["user_id"]):
            raise DealRoomError(409, "This investor has not signed every agreement yet.")
        new_status = "granted"
        if status != "granted":   # a fresh approval starts the access period; a level change keeps it
            kw["ends_on"] = access_ends_on(await room_settings(conn, row["deal_id"]), today)
    elif action == "decline":
        if not (reason or "").strip():
            raise DealRoomError(422, "Give a reason to decline.")
        if status in ("declined",):
            raise DealRoomError(409, "Already declined.")
        new_status, level = "declined", None
    elif action == "revoke":
        if status not in ("granted", "signed_awaiting", "requested", "resign_required", "expired", "paused"):
            raise DealRoomError(409, "Only open or granted access can be revoked.")
        new_status, level = "revoked", None
    else:
        raise DealRoomError(422, "action must be approve, decline, revoke, require_resign or extend")
    _, new = await set_access(conn, row["org_id"], row["deal_id"], row["user_id"], status=new_status,
                              level=level, decided_by=actor, actor=actor, reason=(reason or None), **kw)
    await audit(conn, org_id=row["org_id"], actor=actor, action=f"deal_access.{action}", resource_id=new["id"],
                payload={"deal_id": row["deal_id"], "user_id": row["user_id"], "from": status, "to": new_status,
                         "level": level, "reason": reason, "previous_row": row["id"],
                         "access_ends_on": new["access_ends_on"]})
    await close_todos(conn, row["deal_id"], row["user_id"], ["approve_request"])
    if new_status in ("revoked", "declined"):
        from services.deal_room_control import end_advisors

        await end_advisors(conn, row["deal_id"], row["user_id"], actor=actor,
                           reason=f"The investor's access was {new_status}.")
    return dict(new)


async def last_granted_level(conn, deal_id, user_id) -> str | None:
    """The level of the investor's most recent granted row (history included)."""
    return await conn.fetchval(
        "SELECT level_granted FROM deal_access WHERE deal_id = $1 AND user_id = $2 AND status = 'granted' "
        "ORDER BY valid_from DESC LIMIT 1", _uuid(deal_id), _uuid(user_id))


async def extend_access(conn, row, *, actor, ends_on: date | None, reason: str | None, today: date) -> dict:
    """A manager extends an access period (audited): a granted row gets the
    new end date; an expired row reopens at the level it last had. The new
    date must be today or later (the end date itself is still open)."""
    if ends_on is None:
        raise DealRoomError(422, "Choose the new end date.")
    if ends_on < today:
        raise DealRoomError(422, "The new end date cannot be in the past.")
    status = row["status"]
    if status == "granted":
        level = row["level_granted"]
    elif status == "expired":
        level = await last_granted_level(conn, row["deal_id"], row["user_id"]) or "after_agreements"
    else:
        raise DealRoomError(409, "Only granted or expired access can be extended.")
    _, new = await set_access(conn, row["org_id"], row["deal_id"], row["user_id"], status="granted", level=level,
                              decided_by=actor, actor=actor, reason=(reason or "extended"), ends_on=ends_on)
    await audit(conn, org_id=row["org_id"], actor=actor, action="deal_access.extend", resource_id=new["id"],
                payload={"deal_id": row["deal_id"], "user_id": row["user_id"], "from": status,
                         "previous_ends_on": row["access_ends_on"], "access_ends_on": ends_on, "level": level,
                         "reason": reason, "previous_row": row["id"]})
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
