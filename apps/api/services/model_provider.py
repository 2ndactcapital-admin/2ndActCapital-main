"""REP15 — platform-published models and tenant subscriptions (Replication Studio phase 4 ends).

The Hollisworks platform org can act as a SUB-ADVISER: it publishes models from its own rep3 registry to a CATALOG;
a tenant org subscribes, uses a subscribed model's versions in its accounts, and owes a MODEL-PROVIDER FEE. All of it
sits behind ONE regulatory gate.

THE GATE (portfolio.model_publishing_gates — no row = OFF)
──────────────────────────────────────────────────────────────────────────────
A provider org has a PROVIDER RECORD (portfolio.model_providers: legal entity name, SEC file / CRD number,
registration status, effective date) and an ADV Part 2A that is a disclosure-library file (key
``model_provider.adv_2a``, the provider org's OWN published version). Only Super Admin opens the gate, only with a
complete record and a published ADV in force; the database trigger re-checks all three. Every change is logged
(model_provider_events + audit_log). While the gate is OFF every publish, subscribe, adopt, join, copy, accrual and
invoice is refused with :data:`GATE_REFUSAL`. A provider org is the platform org or an is_test fixture org, and a
tenant's is_test must match its provider's (SECURITY DEFINER ``portfolio.provider_tenant_allowed``) — so a fixture
provider can never reach a real org.

The provider org is always the CALLER's own org (Rule 6: never a body or a path value). The platform side is Super
Admin only, acting as a user of the provider org.

PUBLISHING (provider org, maker-checker)
──────────────────────────────────────────────────────────────────────────────
A provider model is marked PUBLISHABLE (catalog_models: default fee bps, construction notes). One of its APPROVED rep3
versions is requested for the catalog (the maker), with its name ("Holdings of …" — Joe's naming rule, a CHECK too),
description ("replicates the holdings of …"), kind, construction notes, holdings as-of and fee bps; a
``catalog_version_publish`` task opens; a DIFFERENT Super Admin — never the requester nor anyone who made the rep3
version — approves, and the version is PUBLISHED. A published version is immutable (trigger); it may only be
retired, and a retired version stays readable. The catalog row carries a frozen snapshot of the model's TARGET
WEIGHTS only — never the fund's holdings file, an upload, or the provider's registry rows.

SUBSCRIBING (tenant → platform, maker-checker across the two orgs)
──────────────────────────────────────────────────────────────────────────────
An org admin of the tenant acknowledges the in-force ``model_provider.subadvisory_agreement`` version RESOLVED FOR
THAT TENANT (a tenant-specific org version overrides the platform default — the disclosure library's own resolver)
and requests a subscription. A ``provided_subscription_approval`` task opens in the PROVIDER org (no maker there:
the maker is the tenant's requester, recorded on the row); a platform Super Admin who is not the requester approves,
after the agreement in force is re-checked against the one acknowledged. Statuses: requested → active ⇄ suspended →
ended (or requested → rejected), each with its dates.

USING A SUBSCRIBED MODEL (tenant)
──────────────────────────────────────────────────────────────────────────────
Approval creates a READ-ONLY mirror model in the tenant's registry (portfolio.models source 'provided', "Provided by
<provider>"): its versions come only from the catalog (a trigger refuses anything else), and it is joinable by
accounts like the tenant's own. Every published version is imported as a mirror DRAFT plus a
``provided_version_available`` task carrying the exact weight diff. ADOPTING it is the tenant's maker-checker
decision: a holder of manage_models submits it (rep3's submit — the maker), a different holder of approve_models
approves it (rep3's approval task), and rep5 turns the approval into rebalance PROPOSALS. The org setting
``replication.provided_models.pre_approve_adoption`` (default false) makes the person who recorded the setting the
standing approver. COPY makes a tenant-owned model ("Holdings of …", source 'provided_copy') with no link and no fee.
Suspending or ending a subscription stops every adoption and join; accounts already on it keep their version and
each gets a ``provided_model_move_account`` task.

ISOLATION. The provider reads, per tenant, only status, account COUNTS and model ASSETS per catalog version — two
SECURITY DEFINER functions. A tenant never reads another tenant's subscription (RLS).

THE FEE (asset definition: portfolio.provider_model_usage — ONE place)
──────────────────────────────────────────────────────────────────────────────
Daily accrual = model assets × bps / (10 000 × 365), to 10 places (a CHECK holds the formula). Invoice line
"Model-provider fee" per catalog version = round(Σ daily model assets × bps / (10 000 × 365), 2) = bps × the AVERAGE
daily model assets over the period's calendar days × days / 365. Posted through services.ledger.posting (book
MODEL_PROVIDER, template MODEL_PROVIDER_FEE: Dr 1230 Model-Provider Fee Receivable — the amount the tenant owes / Cr
4800 Model-Provider Fee Revenue) in the provider record's ledger org. A correction is a REVERSAL
(fn_reverse_journal_entry) plus a reversal invoice — nothing is edited.

THE CUSTODY CLIFF. Nothing here trades or moves money; an invoice is a receivable, not a collection.
"""
from __future__ import annotations

import hashlib
import json
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

from services import disclosures as DL
from services import human_tasks as HT
from services import models as M
from services.database import reset_rls_context, set_rls_context

log = logging.getLogger(__name__)

PLATFORM_ORG_ID = M.PLATFORM_ORG_ID
GATE_REFUSAL = "model publishing is not enabled — a registered adviser entity is required"

T_PROV = "portfolio.model_providers"
T_GATE = "portfolio.model_publishing_gates"
T_CM = "portfolio.catalog_models"
T_CV = "portfolio.catalog_versions"
T_SUB = "portfolio.provider_subscriptions"
T_ACC = "portfolio.provider_fee_accruals"
T_INV = "portfolio.provider_fee_invoices"
T_LINE = "portfolio.provider_fee_invoice_lines"
T_EVT = "portfolio.model_provider_events"

KEY_ADV = "model_provider.adv_2a"
KEY_AGREEMENT = "model_provider.subadvisory_agreement"
KEY_NOTICE = "model_provider.member_notice"
ACK_SUBJECT = "model_subscription"
PREAPPROVE_SETTING = "replication.provided_models.pre_approve_adoption"

TASK_PUBLISH = "catalog_version_publish"
TASK_SUB = "provided_subscription_approval"
TASK_AVAILABLE = "provided_version_available"
TASK_MOVE = "provided_model_move_account"
PERM_PUBLISH = "publish_provided_models"          # granted to no role: Super Admin only
PERM_APPROVE_SUB = "approve_provided_subscriptions"  # granted to no role: Super Admin only
VIEW, MANAGE, APPROVE = M.VIEW, M.MANAGE, M.APPROVE
ORG_ADMIN = "manage_org_settings"
MOVE_PERMISSION = "manage_proposals"

DAY_BASIS = 365
FEE_BPS_MAX = Decimal(300)
LINE_LABEL = "Model-provider fee"
LEDGER_BOOK = "MODEL_PROVIDER"
TXN_CODE = "MODEL_PROVIDER_FEE"
ACCT_RECEIVABLE = ("1230", "Model-Provider Fee Receivable", "ASSET", "D")
ACCT_REVENUE = ("4800", "Model-Provider Fee Revenue", "INCOME", "C")
NAME_PREFIX = "holdings of "
DESCRIPTION_PHRASE = "replicates the holdings of "
MAX_ACCRUAL_DAYS = 366

#: The offline fallback for the member notice (a NEW platform default — the library version replaces it once the
#: operator loads docs/disclosures/defaults/model_provider.member_notice.md). {provider} is filled in per proposal.
MEMBER_NOTICE_FALLBACK = (
    "This model is provided by a sub-adviser, {provider}, which selects and maintains its target weights. Your "
    "adviser decides whether to use the model for your account and remains responsible for that decision. The "
    "sub-adviser is paid a model-provider fee for its use. Default — have counsel review.")

REGISTRATION_LABELS = {"not_registered": "Not registered", "pending": "Registration pending",
                       "registered": "Registered", "withdrawn": "Registration withdrawn"}
CATALOG_STATUS_LABELS = {"pending": "Awaiting publication", "published": "Published", "rejected": "Rejected",
                         "retired": "Retired"}
SUBSCRIPTION_STATUS_LABELS = {"requested": "Requested — awaiting platform approval", "active": "Active",
                              "suspended": "Suspended", "ended": "Ended", "rejected": "Rejected"}
KIND_LABELS = {"long_only": "Long only", "long_short": "Long/short"}
OPERATOR_ITEMS = (
    "The registered adviser entity behind the platform (a structuring decision — Hollisworks the software company is "
    "not an RIA today): its legal name, SEC file / CRD number and effective date on the provider record.",
    "The ADV Part 2A as a library file (model_provider.adv_2a), published at the provider org's scope.",
    "The sub-advisory agreement text (model_provider.subadvisory_agreement) — a platform default and any "
    "tenant-specific version — written by counsel; nothing is shipped in code.",
    "The model-provider fee (bps) for each published model.",
    "Each subscribing tenant's own ADV disclosure of the sub-adviser relationship — the tenant's obligation, listed "
    "here, not automated.",
    "Which org's books carry the fee revenue (the provider record's ledger org — the provider org by default; the "
    "spec says the fee is owed to 2nd Act).",
)


class ProviderError(ValueError):
    """A request the rules refuse (422)."""


class ProviderNotFound(LookupError):
    """Not found in the caller's reach (404)."""


class ProviderConflict(RuntimeError):
    """The state does not allow it (409)."""


class GateClosed(ProviderConflict):
    """The publishing gate is OFF (409, always :data:`GATE_REFUSAL`)."""


class ProviderPermissionError(PermissionError):
    """The caller may not act here (403)."""


class MakerCheckerViolation(PermissionError):
    """The maker may not decide their own request (403)."""


# ── helpers ─────────────────────────────────────────────────────────────────
def _dumps(v) -> str:
    return json.dumps(v, default=str, sort_keys=True)


def _loads(v):
    if v is None or isinstance(v, (dict, list)):
        return v
    return json.loads(v)


def _uuid(v, what: str) -> str:
    try:
        return str(uuid.UUID(str(v)))
    except (TypeError, ValueError):
        raise ProviderNotFound(f"{what} not found") from None


def _iso(v):
    return v.isoformat() if v is not None else None


def _s(v):
    return str(v) if v is not None else None


def _num(v):
    return None if v is None else str(v)


def dec(v, what: str) -> Decimal:
    if isinstance(v, float):
        raise ProviderError(f"{what} is a decimal string, not a float")
    try:
        d = Decimal(str(v))
    except Exception:  # noqa: BLE001
        raise ProviderError(f"{what} is not a number") from None
    if not d.is_finite():
        raise ProviderError(f"{what} is not a number")
    return d


def cents(d: Decimal) -> Decimal:
    return d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def daily_fee(assets: Decimal, bps: Decimal) -> Decimal:
    """bps × assets / (10 000 × 365) to 10 places — the accrual CHECK's own formula."""
    return (assets * bps / (Decimal(10000) * DAY_BASIS)).quantize(Decimal("1E-10"), rounding=ROUND_HALF_UP)


def period_amount(sum_daily_assets: Decimal, bps: Decimal) -> Decimal:
    """Σ daily assets × bps / (10 000 × 365), to the cent = bps × average daily assets × days / 365."""
    return cents(sum_daily_assets * bps / (Decimal(10000) * DAY_BASIS))


def check_fee(v) -> Decimal:
    d = dec(v, "the model fee (bps)")
    if not (Decimal(0) < d <= FEE_BPS_MAX):
        raise ProviderError(f"the model fee is more than 0 and at most {FEE_BPS_MAX} bps")
    return d.quantize(Decimal("0.0001"))


def check_naming(name: str, description: str) -> tuple[str, str]:
    """Joe's rule: the name starts "Holdings of", the description says "replicates the holdings of …"."""
    n = (name or "").strip()
    d = (description or "").strip()
    if not n.lower().startswith(NAME_PREFIX) or len(n) <= len(NAME_PREFIX):
        raise ProviderError('a published model is named "Holdings of …" (never the index\'s own name)')
    if len(n) > 200:
        raise ProviderError("a model name is at most 200 characters")
    if DESCRIPTION_PHRASE not in d.lower():
        raise ProviderError('the description says what the model does: "replicates the holdings of …"')
    return n, d


@asynccontextmanager
async def as_org(org_id, super_admin: bool = False):
    """Run the enclosed pool work under another org's RLS context (the pool applies it SET LOCAL per unit)."""
    tokens = set_rls_context(str(org_id), super_admin)
    try:
        yield
    finally:
        reset_rls_context(tokens)


async def _log(conn, *, org_id, provider_org_id, action: str, actor_id, subject_type: str, subject_id,
               tenant_org_id=None, detail: dict | None = None) -> str:
    """One model_provider_events row + its audit_log row, in the ACTING org, inside the caller's transaction."""
    eid = await conn.fetchval(
        f"INSERT INTO {T_EVT} (org_id, provider_org_id, tenant_org_id, action, actor_id, subject_type, subject_id, "
        "detail) VALUES ($1::uuid, $2::uuid, $3::uuid, $4, $5::uuid, $6, $7::uuid, $8::jsonb) RETURNING id",
        str(org_id), str(provider_org_id), _s(tenant_org_id), action, _s(actor_id), subject_type, _s(subject_id),
        _dumps(detail or {}))
    await conn.execute(
        "INSERT INTO audit_log (org_id, user_id, action, resource_type, resource_id, payload) "
        "VALUES ($1::uuid, $2::uuid, $3, 'model_provider_event', $4::uuid, $5::jsonb)",
        str(org_id), _s(actor_id), f"model_provider.{action}", str(eid),
        _dumps({"subject_type": subject_type, "subject_id": _s(subject_id), "provider_org_id": str(provider_org_id),
                "tenant_org_id": _s(tenant_org_id), **(detail or {})}))
    return str(eid)


async def _update(conn, table: str, row_id, fields: dict, jsonb: frozenset = frozenset()) -> dict:
    """System-axis edit: archive the outgoing state (a copy with archived_from_id), then change the live row."""
    await M._archive(conn, table, row_id)
    cols = list(fields)
    sets = ", ".join(f"{c} = ${i + 2}" + ("::jsonb" if c in jsonb else "") for i, c in enumerate(cols))
    vals = [(_dumps(fields[c]) if c in jsonb else fields[c]) for c in cols]
    row = await conn.fetchrow(f"UPDATE {table} SET {sets}, system_from = now() WHERE id = $1::uuid RETURNING *",
                              str(row_id), *vals)
    if row is None:
        raise ProviderConflict(f"{table} row {row_id} was not updated (not visible or not live)")
    return dict(row)


async def db_today(conn) -> date:
    """THE date every rule here uses — the database's current_date (the triggers and the usage function compare
    against it), never the app host's local date, which can be a day apart."""
    return await conn.fetchval("SELECT current_date")


async def is_open(conn, provider_org) -> bool:
    return bool(await conn.fetchval("SELECT portfolio.model_publishing_open($1::uuid)", str(provider_org)))


async def require_open(conn, provider_org) -> None:
    if not await is_open(conn, provider_org):
        raise GateClosed(GATE_REFUSAL)


async def org_allowed(conn, org_id) -> bool:
    return bool(await conn.fetchval("SELECT portfolio.model_provider_org_allowed($1::uuid)", str(org_id)))


async def require_provider_org(conn, org_id) -> None:
    if not await org_allowed(conn, org_id):
        raise ProviderPermissionError("this org is not a model provider — only the Hollisworks platform org publishes "
                                      "models (Super Admin, acting in that org)")


async def org_name(conn, org_id) -> str | None:
    return await conn.fetchval("SELECT name FROM organizations WHERE id = $1::uuid", str(org_id))


async def _assert_super_admin(conn, actor_id):
    """The ONE shared helper (services.rbac.is_super_admin over services.authz.load_principal — user_roles), re-run
    in the service so a task decided through the generic task route is held to the same rule as the router."""
    from services import rbac
    from services.authz import load_principal

    try:
        p = await load_principal(conn, actor_id)
    except (TypeError, ValueError):
        p = None
    if p is None or not rbac.is_super_admin(p):
        raise ProviderPermissionError("Super Admin required: the platform side of model publishing is Hollisworks "
                                      "staff only")
    return p


# ═══ The provider record and the gate ═══════════════════════════════════════════════════════════════════════════
async def provider_row(conn, provider_org) -> dict | None:
    r = await conn.fetchrow(f"SELECT * FROM {T_PROV} WHERE provider_org_id = $1::uuid AND system_to IS NULL",
                            str(provider_org))
    return dict(r) if r else None


async def gate_row(conn, provider_org) -> dict | None:
    r = await conn.fetchrow(f"SELECT * FROM {T_GATE} WHERE provider_org_id = $1::uuid AND system_to IS NULL",
                            str(provider_org))
    return dict(r) if r else None


async def adv_resolved(conn, provider_org) -> dict:
    return await DL.resolve_safe(conn, KEY_ADV, org_id=provider_org)


def adv_ok(adv: dict, provider_org) -> bool:
    return bool(adv.get("source") == "library" and adv.get("scope") == "org" and adv.get("version_id"))


def readiness(record: dict | None, adv: dict, provider_org, today: date) -> list[dict]:
    """What opening the gate still needs — every item named, none satisfied by default."""
    r = record or {}
    items = [
        ("legal_name", "The registered adviser entity's legal name", bool(r.get("legal_name"))),
        ("number", "Its SEC file number or CRD number", bool(r.get("sec_file_number") or r.get("crd_number"))),
        ("registered", "Its registration status is 'registered'", r.get("registration_status") == "registered"),
        ("effective", "Its effective date is today or earlier",
         bool(r.get("effective_date") and r["effective_date"] <= today)),
        ("adv", "Its ADV Part 2A is a published library version (model_provider.adv_2a, this org's scope)",
         adv_ok(adv, provider_org)),
    ]
    return [{"key": k, "label": lab, "met": bool(ok)} for k, lab, ok in items]


def provider_out(r: dict | None) -> dict | None:
    if r is None:
        return None
    return {"id": str(r["id"]), "provider_org_id": str(r["provider_org_id"]), "legal_name": r["legal_name"],
            "sec_file_number": r["sec_file_number"], "crd_number": r["crd_number"],
            "registration_status": r["registration_status"],
            "registration_label": REGISTRATION_LABELS.get(r["registration_status"]),
            "effective_date": _iso(r["effective_date"]), "adv_key": r["adv_key"],
            "ledger_org_id": str(r["ledger_org_id"]), "notes": r["notes"], "updated_at": _iso(r["system_from"])}


def gate_out(g: dict | None) -> dict:
    if g is None:
        return {"is_open": False, "label": "Off — no gate has ever been opened", "changed_at": None, "reason": None,
                "adv_version_id": None}
    return {"is_open": bool(g["is_open"]), "label": "Open" if g["is_open"] else "Off",
            "changed_at": _iso(g["changed_at"]), "changed_by": _s(g["changed_by"]), "reason": g["reason"],
            "adv_version_id": _s(g["adv_version_id"]), "adv_sha256": g["adv_sha256"]}


async def provider_state(conn, provider_org) -> dict:
    record = await provider_row(conn, provider_org)
    adv = await adv_resolved(conn, provider_org)
    ready = readiness(record, adv, provider_org, await db_today(conn))
    history = [dict(r) for r in await conn.fetch(
        f"SELECT is_open, changed_by, changed_at, reason FROM {T_GATE} WHERE provider_org_id = $1::uuid "
        "ORDER BY changed_at DESC, system_from DESC LIMIT 50", str(provider_org))]
    return {"provider_org_id": str(provider_org), "provider_org_name": await org_name(conn, provider_org),
            "provider": provider_out(record), "gate": gate_out(await gate_row(conn, provider_org)),
            "adv": DL.public(adv), "readiness": ready, "ready_to_open": all(i["met"] for i in ready),
            "gate_history": [{"is_open": h["is_open"], "changed_by": _s(h["changed_by"]),
                              "changed_at": _iso(h["changed_at"]), "reason": h["reason"]} for h in history],
            "operator_items": list(OPERATOR_ITEMS), "refusal": GATE_REFUSAL}


PROVIDER_FIELDS = ("legal_name", "sec_file_number", "crd_number", "registration_status", "effective_date", "notes")


async def save_provider(pool, *, provider_org, actor_id, data: dict) -> dict:
    """Create or restate the provider record (system axis). Super Admin; the provider org is the caller's own."""
    clean = {k: data.get(k) for k in PROVIDER_FIELDS}
    for k in ("legal_name", "sec_file_number", "crd_number", "notes"):
        clean[k] = (clean[k] or "").strip() or None
    status = clean["registration_status"] or "not_registered"
    if status not in REGISTRATION_LABELS:
        raise ProviderError(f"registration status is one of {list(REGISTRATION_LABELS)}")
    clean["registration_status"] = status
    if status == "registered" and not (clean["legal_name"] and clean["effective_date"]
                                       and (clean["sec_file_number"] or clean["crd_number"])):
        raise ProviderError("a registered adviser needs its legal name, an SEC file or CRD number and an effective "
                            "date")
    async with pool.acquire() as conn:
        await require_provider_org(conn, provider_org)
        await _assert_super_admin(conn, actor_id)
        cur = await provider_row(conn, provider_org)
        if cur is None:
            row = dict(await conn.fetchrow(
                f"INSERT INTO {T_PROV} (provider_org_id, legal_name, sec_file_number, crd_number, registration_status, "
                "effective_date, ledger_org_id, notes, created_by, updated_by) VALUES ($1::uuid, $2, $3, $4, $5, $6, "
                "$1::uuid, $7, $8::uuid, $8::uuid) RETURNING *", str(provider_org), clean["legal_name"],
                clean["sec_file_number"], clean["crd_number"], status, clean["effective_date"], clean["notes"],
                str(actor_id)))
        else:
            row = await _update(conn, T_PROV, cur["id"], {**clean, "updated_by": uuid.UUID(str(actor_id))})
        await _log(conn, org_id=provider_org, provider_org_id=provider_org, action="provider_saved", actor_id=actor_id,
                   subject_type="provider", subject_id=row["id"],
                   detail={k: _s(row[k]) for k in PROVIDER_FIELDS if k != "notes"})
    return provider_out(row)


async def ensure_ledger(conn, ledger_org) -> str:
    """The fee's book, two accounts and its posting template in the ledger org (idempotent). Runs under the
    Super Admin context of the gate's opener or the fee engine. Returns the ledger book id."""
    book = await conn.fetchval("SELECT id FROM ledger_books WHERE org_id = $1::uuid AND book_code = $2 "
                               "AND system_to IS NULL", str(ledger_org), LEDGER_BOOK)
    if book is None:
        book = await conn.fetchval(
            "INSERT INTO ledger_books (org_id, book_code, name, description) VALUES ($1::uuid, $2, $3, $4) RETURNING id",
            str(ledger_org), LEDGER_BOOK, "Model-provider fees",
            "rep15 — fees owed by tenant orgs for the use of provided (sub-advised) models")
    for code, name, kind, normal in (ACCT_RECEIVABLE, ACCT_REVENUE):
        if not await conn.fetchval("SELECT 1 FROM chart_of_accounts WHERE org_id = $1::uuid AND code = $2 "
                                   "AND system_to IS NULL", str(ledger_org), code):
            await conn.execute("INSERT INTO chart_of_accounts (org_id, code, name, account_type, normal_balance) "
                               "VALUES ($1::uuid, $2, $3, $4, $5)", str(ledger_org), code, name, kind, normal)
    tmpl = await conn.fetchval("SELECT id FROM posting_templates WHERE org_id = $1::uuid AND transaction_type_code = $2 "
                               "AND vehicle_type_scope = 'any' AND is_active", str(ledger_org), TXN_CODE)
    if tmpl is None:
        tmpl = await conn.fetchval(
            "INSERT INTO posting_templates (org_id, transaction_type_code, name, vehicle_type_scope, is_active) "
            "VALUES ($1::uuid, $2, $3, 'any', true) RETURNING id", str(ledger_org), TXN_CODE, LINE_LABEL)
        await conn.execute(
            "INSERT INTO posting_template_lines (template_id, line_no, account_code, side) VALUES ($1, 1, $2, 'D'), "
            "($1, 2, $3, 'C')", tmpl, ACCT_RECEIVABLE[0], ACCT_REVENUE[0])
    return str(book)


async def open_gate(pool, *, provider_org, actor_id, reason: str) -> dict:
    """Super Admin opens the gate — refused (each missing item named) without a complete record and a published
    ADV; the database trigger re-checks. Provisions the fee's ledger in the provider record's ledger org."""
    if not (reason or "").strip():
        raise ProviderError("opening the gate needs a reason")
    async with pool.acquire() as conn:
        await require_provider_org(conn, provider_org)
        await _assert_super_admin(conn, actor_id)
        record = await provider_row(conn, provider_org)
        adv = await adv_resolved(conn, provider_org)
        missing = [i["label"] for i in readiness(record, adv, provider_org, await db_today(conn)) if not i["met"]]
    if missing:
        async with pool.acquire() as conn:
            await _log(conn, org_id=provider_org, provider_org_id=provider_org, action="gate_refused",
                       actor_id=actor_id, subject_type="gate", subject_id=None, detail={"missing": missing})
        raise GateClosed(f"{GATE_REFUSAL} — missing: " + "; ".join(missing))
    async with pool.acquire() as conn:
        book = await ensure_ledger(conn, record["ledger_org_id"])
        snap = {k: _s(record[k]) for k in ("legal_name", "sec_file_number", "crd_number", "registration_status",
                                           "effective_date", "ledger_org_id")}
        fields = {"is_open": True, "provider_record_id": record["id"], "provider_snapshot": snap,
                  "adv_version_id": uuid.UUID(adv["version_id"]), "adv_sha256": adv["content_sha256"],
                  "changed_by": uuid.UUID(str(actor_id)), "changed_at": datetime.now(timezone.utc),
                  "reason": reason.strip()}
        cur = await gate_row(conn, provider_org)
        if cur is None:
            g = dict(await conn.fetchrow(
                f"INSERT INTO {T_GATE} (provider_org_id, is_open, provider_record_id, provider_snapshot, adv_version_id, "
                "adv_sha256, changed_by, reason) VALUES ($1::uuid, true, $2, $3::jsonb, $4, $5, $6::uuid, $7) "
                "RETURNING *", str(provider_org), record["id"], _dumps(snap), fields["adv_version_id"],
                fields["adv_sha256"], str(actor_id), fields["reason"]))
        else:
            g = await _update(conn, T_GATE, cur["id"], fields, frozenset({"provider_snapshot"}))
        await _log(conn, org_id=provider_org, provider_org_id=provider_org, action="gate_opened", actor_id=actor_id,
                   subject_type="gate", subject_id=g["id"],
                   detail={"reason": reason.strip(), "adv_version_id": adv["version_id"],
                           "adv_sha256": adv["content_sha256"], "ledger_book_id": book, **snap})
    return gate_out(g)


async def close_gate(pool, *, provider_org, actor_id, reason: str) -> dict:
    if not (reason or "").strip():
        raise ProviderError("closing the gate needs a reason")
    async with pool.acquire() as conn:
        await require_provider_org(conn, provider_org)
        await _assert_super_admin(conn, actor_id)
        cur = await gate_row(conn, provider_org)
        if cur is None or not cur["is_open"]:
            raise ProviderConflict("the gate is already off")
        g = await _update(conn, T_GATE, cur["id"], {"is_open": False, "changed_by": uuid.UUID(str(actor_id)),
                                                    "changed_at": datetime.now(timezone.utc),
                                                    "reason": reason.strip()})
        await _log(conn, org_id=provider_org, provider_org_id=provider_org, action="gate_closed", actor_id=actor_id,
                   subject_type="gate", subject_id=g["id"], detail={"reason": reason.strip()})
    return gate_out(g)


# ═══ Publishing (provider org) ══════════════════════════════════════════════════════════════════════════════════
async def catalog_model_row(conn, catalog_model_id, *, provider_org=None) -> dict:
    cid = _uuid(catalog_model_id, "catalog model")
    r = await conn.fetchrow(f"SELECT * FROM {T_CM} WHERE id = $1::uuid AND system_to IS NULL", cid)
    if r is None or (provider_org is not None and str(r["provider_org_id"]) != str(provider_org)):
        raise ProviderNotFound("catalog model not found")
    return dict(r)


async def catalog_version_row(conn, cv_id, *, provider_org=None, lock: bool = False) -> dict:
    vid = _uuid(cv_id, "catalog version")
    r = await conn.fetchrow(f"SELECT * FROM {T_CV} WHERE id = $1::uuid AND system_to IS NULL "
                            f"{'FOR UPDATE' if lock else ''}", vid)
    if r is None or (provider_org is not None and str(r["provider_org_id"]) != str(provider_org)):
        raise ProviderNotFound("catalog version not found")
    return dict(r)


async def mark_publishable(pool, *, provider_org, actor_id, model_id, default_fee_bps,
                           construction_notes: str | None) -> dict:
    fee = check_fee(default_fee_bps)
    async with pool.acquire() as conn:
        await require_provider_org(conn, provider_org)
        await _assert_super_admin(conn, actor_id)
        await require_open(conn, provider_org)
        model = await M.load_model(conn, provider_org, model_id)
        if model.get("kind") == "long_short":
            raise ProviderError(f"{model['name']} is a long/short model — publishing a long/short model is not built "
                                "(its short book lives outside the version's positions)")
        if model["source"] in ("provided", "provided_copy", "library_copy", "library_subscription"):
            raise ProviderError("a model loaded from a provider or the library cannot be republished")
        if await conn.fetchval(f"SELECT 1 FROM {T_CM} WHERE model_id = $1 AND system_to IS NULL", model["id"]):
            raise ProviderConflict(f"{model['name']} is already marked publishable")
        row = dict(await conn.fetchrow(
            f"INSERT INTO {T_CM} (provider_org_id, model_id, default_fee_bps, construction_notes, created_by) "
            "VALUES ($1::uuid, $2, $3, $4, $5::uuid) RETURNING *", str(provider_org), model["id"], fee,
            (construction_notes or "").strip() or None, str(actor_id)))
        await _log(conn, org_id=provider_org, provider_org_id=provider_org, action="model_publishable",
                   actor_id=actor_id, subject_type="catalog_model", subject_id=row["id"],
                   detail={"model_id": str(model["id"]), "default_fee_bps": str(fee)})
    return catalog_model_out(row)


async def withdraw_publishable(pool, *, provider_org, actor_id, catalog_model_id, reason: str) -> dict:
    if not (reason or "").strip():
        raise ProviderError("withdrawing needs a reason")
    async with pool.acquire() as conn:
        await _assert_super_admin(conn, actor_id)
        cm = await catalog_model_row(conn, catalog_model_id, provider_org=provider_org)
        if cm["status"] == "withdrawn":
            raise ProviderConflict("already withdrawn")
        row = await _update(conn, T_CM, cm["id"], {"status": "withdrawn", "status_changed_by": uuid.UUID(str(actor_id)),
                                                   "status_changed_at": datetime.now(timezone.utc),
                                                   "status_reason": reason.strip()})
        await _log(conn, org_id=provider_org, provider_org_id=provider_org, action="model_withdrawn",
                   actor_id=actor_id, subject_type="catalog_model", subject_id=cm["id"],
                   detail={"reason": reason.strip()})
    return catalog_model_out(row)


async def weights_snapshot(conn, version_id) -> list[dict]:
    """The model's TARGET WEIGHTS (look-through, exact Decimal) — the only content a tenant ever receives."""
    lt = await M.look_through(conn, version_id)
    rows = []
    gids = [v["global_security_id"] for v in lt.values() if v["global_security_id"]]
    tick = {str(r["gid"]): r["ticker"] for r in await conn.fetch(
        "SELECT DISTINCT ON (global_security_id) global_security_id AS gid, id_value AS ticker "
        "FROM portfolio.securities_global_identifiers WHERE global_security_id = ANY($1::uuid[]) AND id_type = 'ticker' "
        "AND valid_to IS NULL AND system_to IS NULL ORDER BY global_security_id, is_primary DESC NULLS LAST, id_value",
        gids)} if gids else {}
    for k, v in sorted(lt.items(), key=lambda kv: (-kv[1]["weight"], kv[0])):
        if v["resolution_state"] == "unresolved":
            raise ProviderConflict(f"{v['label']} is unresolved — only a fully resolved model is published")
        rows.append({"key": k, "global_security_id": v["global_security_id"],
                     "ticker": tick.get(v["global_security_id"] or ""), "name": v["label"],
                     "weight": str(M.U.q(Decimal(str(v["weight"])))), "cash": v["resolution_state"] == "cash"})
    for i, r in enumerate(rows, start=1):
        r["row"] = i
    return rows


def weights_sha(weights: list[dict]) -> str:
    return hashlib.sha256(json.dumps(weights, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def weight_diff(before: list[dict] | None, after: list[dict]) -> list[dict]:
    """The exact change per security (Decimal strings), largest change first; unchanged rows omitted."""
    b = {r["key"]: r for r in (before or [])}
    a = {r["key"]: r for r in after}
    out = []
    for k in set(b) | set(a):
        wb = Decimal(b[k]["weight"]) if k in b else Decimal(0)
        wa = Decimal(a[k]["weight"]) if k in a else Decimal(0)
        if wa != wb:
            src = a.get(k) or b.get(k)
            out.append({"key": k, "ticker": src.get("ticker"), "name": src.get("name"), "before": str(wb),
                        "after": str(wa), "change": str(wa - wb),
                        "kind": "added" if k not in b else "removed" if k not in a else "changed"})
    return sorted(out, key=lambda r: (-abs(Decimal(r["change"])), r["key"]))


async def request_publish(pool, *, provider_org, actor_id, catalog_model_id, version_id, name: str, description: str,
                          construction_notes: str, holdings_as_of: date, fee_bps=None) -> dict:
    """The MAKER: an approved rep3 version → a pending catalog version + its publish task (a different Super Admin
    — never the requester nor a maker of the rep3 version — publishes it)."""
    n, d = check_naming(name, description)
    notes = (construction_notes or "").strip()
    if not notes:
        raise ProviderError("construction notes are required (how the model is built and maintained)")
    async with pool.acquire() as conn:
        if holdings_as_of is None or holdings_as_of > await db_today(conn):
            raise ProviderError("holdings as-of is a date no later than today")
        await require_provider_org(conn, provider_org)
        await _assert_super_admin(conn, actor_id)
        await require_open(conn, provider_org)
        cm = await catalog_model_row(conn, catalog_model_id, provider_org=provider_org)
        if cm["status"] != "publishable":
            raise ProviderConflict("this model is withdrawn from publishing")
        fee = check_fee(fee_bps if fee_bps is not None else cm["default_fee_bps"])
        model = await M.load_model(conn, provider_org, cm["model_id"])
        v = await M.load_version(conn, provider_org, model["id"], version_id)
        if v["status"] != "approved":
            raise ProviderConflict(f"version {v['version_no']} is {v['status']} — only the model's APPROVED version is "
                                   "published (approve → publish)")
        if await conn.fetchval(f"SELECT 1 FROM {T_CV} WHERE model_version_id = $1 AND status IN ('pending', "
                               "'published', 'retired') AND system_to IS NULL", v["id"]):
            raise ProviderConflict(f"version {v['version_no']} is already in the catalog")
        weights = await weights_snapshot(conn, v["id"])
        record = await provider_row(conn, provider_org)
        no = int(await conn.fetchval(f"SELECT COALESCE(max(catalog_version_no), 0) + 1 FROM {T_CV} "
                                     "WHERE catalog_model_id = $1 AND system_to IS NULL", cm["id"]))
    cvid = str(uuid.uuid4())
    task = await HT.open_task(pool, org_id=provider_org, kind=TASK_PUBLISH, subject_type="catalog_version",
                              subject_id=cvid, maker_id=actor_id, eligible_permission=PERM_PUBLISH,
                              title=f"Publish {n} — catalog version {no}",
                              payload={"catalog_model_id": str(cm["id"]), "catalog_version_no": no,
                                       "fee_bps": str(fee), "positions": len(weights)},
                              action_path="/portfolio/replication/provider")
    try:
        async with pool.acquire() as conn:
            row = dict(await conn.fetchrow(
                f"""INSERT INTO {T_CV} (id, provider_org_id, catalog_model_id, model_id, model_version_id,
                        catalog_version_no, status, name, description, kind, construction_notes, holdings_as_of, fee_bps,
                        provider_legal_name, weights, weights_sha256, version_maker_ids, requested_by,
                        approval_task_id)
                    VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, 'pending', $7, $8, $9, $10, $11, $12, $13, $14::jsonb,
                            $15, $16::uuid[], $17::uuid, $18::uuid) RETURNING *""",
                cvid, str(provider_org), cm["id"], model["id"], v["id"], no, n, d, model.get("kind") or "long_only",
                notes, holdings_as_of, fee, record["legal_name"], _dumps(weights), weights_sha(weights),
                list(v["maker_ids"] or []), str(actor_id), task["id"]))
            await _log(conn, org_id=provider_org, provider_org_id=provider_org, action="publish_requested",
                       actor_id=actor_id, subject_type="catalog_version", subject_id=cvid,
                       detail={"catalog_version_no": no, "model_version_id": str(v["id"]), "task_id": task["id"]})
    except Exception:
        await HT.cancel_task(pool, org_id=provider_org, task_id=task["id"], actor_id=actor_id,
                             reason="the catalog version could not be recorded")
        raise
    return catalog_version_out(row, include_weights=True)


async def _publish_precheck(pool, task, outcome, actor_id) -> None:
    async with pool.acquire() as conn:
        cv = await conn.fetchrow(f"SELECT requested_by, version_maker_ids, provider_org_id FROM {T_CV} "
                                 "WHERE id = $1::uuid AND system_to IS NULL", task["subject_id"])
        if cv is None:
            raise HT.HumanTaskConflict("the catalog version behind this task is missing")
        if str(cv["requested_by"]) == str(actor_id) or uuid.UUID(str(actor_id)) in (cv["version_maker_ids"] or []):
            raise HT.MakerCheckerError("maker-checker: you requested this publication or made the model version — "
                                       "a different Super Admin publishes it")
        await _assert_super_admin(conn, actor_id)
        if outcome == "approve" and not await is_open(conn, cv["provider_org_id"]):
            raise HT.HumanTaskConflict(GATE_REFUSAL)


async def _apply_publish(pool, task, outcome, actor_id, note) -> dict | None:
    provider_org = task["org_id"]
    async with pool.acquire() as conn:
        cv = await conn.fetchrow(f"SELECT * FROM {T_CV} WHERE id = $1::uuid AND system_to IS NULL FOR UPDATE",
                                 task["subject_id"])
        if cv is None or cv["status"] != "pending" or str(cv["approval_task_id"]) != str(task["id"]):
            return dict(cv) if cv else None
        now = datetime.now(timezone.utc)
        if outcome == "approve":
            row = await _update(conn, T_CV, cv["id"], {"status": "published", "checker_id": uuid.UUID(str(actor_id)),
                                                       "published_at": now, "decision_note": note})
            await _log(conn, org_id=provider_org, provider_org_id=provider_org, action="version_published",
                       actor_id=actor_id, subject_type="catalog_version", subject_id=cv["id"],
                       detail={"catalog_version_no": cv["catalog_version_no"], "weights_sha256": cv["weights_sha256"]})
        else:
            row = await _update(conn, T_CV, cv["id"], {"status": "rejected", "checker_id": uuid.UUID(str(actor_id)),
                                                       "rejected_at": now, "decision_note": note})
            await _log(conn, org_id=provider_org, provider_org_id=provider_org, action="version_rejected",
                       actor_id=actor_id, subject_type="catalog_version", subject_id=cv["id"], detail={"note": note})
    out = catalog_version_out(row, include_weights=True)
    if outcome == "approve":
        out["fan_out"] = await fan_out(pool, row)
    return out


async def decide_publish(pool, *, provider_org, actor_id, catalog_version_id, outcome: str,
                         note: str | None = None) -> dict:
    if outcome not in ("approve", "reject"):
        raise ProviderError("outcome is approve or reject")
    async with pool.acquire() as conn:
        await _assert_super_admin(conn, actor_id)
        cv = await catalog_version_row(conn, catalog_version_id, provider_org=provider_org)
        if cv["status"] != "pending":
            raise ProviderConflict(f"catalog version {cv['catalog_version_no']} is {cv['status']} — a published "
                                   "version is never changed")
        if str(cv["requested_by"]) == str(actor_id) or uuid.UUID(str(actor_id)) in (cv["version_maker_ids"] or []):
            raise MakerCheckerViolation("maker-checker: you requested this publication or made the model version — a "
                                        "different Super Admin publishes it")
        if outcome == "approve":
            await require_open(conn, provider_org)
    try:
        done = await HT.complete_task(pool, org_id=provider_org, task_id=cv["approval_task_id"], actor_id=actor_id,
                                      outcome=outcome, note=note)
    except HT.MakerCheckerError as exc:
        raise MakerCheckerViolation(str(exc)) from exc
    except HT.HumanTaskPermissionError as exc:
        raise ProviderPermissionError(str(exc)) from exc
    except HT.HumanTaskConflict as exc:
        if GATE_REFUSAL in str(exc):
            raise GateClosed(GATE_REFUSAL) from exc
        raise ProviderConflict(str(exc)) from exc
    return done.get("subject_result") or {}


async def retire_version(pool, *, provider_org, actor_id, catalog_version_id, reason: str) -> dict:
    if not (reason or "").strip():
        raise ProviderError("retiring needs a reason")
    async with pool.acquire() as conn:
        await _assert_super_admin(conn, actor_id)
        cv = await catalog_version_row(conn, catalog_version_id, provider_org=provider_org, lock=True)
        if cv["status"] != "published":
            raise ProviderConflict(f"only a published version is retired (this one is {cv['status']})")
        row = await _update(conn, T_CV, cv["id"], {"status": "retired", "retired_by": uuid.UUID(str(actor_id)),
                                                   "retired_at": datetime.now(timezone.utc),
                                                   "retire_reason": reason.strip()})
        await _log(conn, org_id=provider_org, provider_org_id=provider_org, action="version_retired",
                   actor_id=actor_id, subject_type="catalog_version", subject_id=cv["id"],
                   detail={"reason": reason.strip()})
    return catalog_version_out(row)


def catalog_model_out(r: dict) -> dict:
    return {"id": str(r["id"]), "model_id": str(r["model_id"]), "status": r["status"],
            "default_fee_bps": _num(r["default_fee_bps"]), "construction_notes": r["construction_notes"]}


#: what a TENANT ever receives of a catalog version — no model / version / upload / replica id of the provider's
TENANT_CV_FIELDS = ("id", "catalog_model_id", "catalog_version_no", "status", "name", "description", "kind",
                    "construction_notes", "holdings_as_of", "fee_bps", "provider_legal_name", "weights_sha256",
                    "published_at", "retired_at")


def catalog_version_out(r: dict, *, include_weights: bool = False, tenant: bool = False) -> dict:
    out = {"id": str(r["id"]), "catalog_model_id": str(r["catalog_model_id"]),
           "catalog_version_no": r["catalog_version_no"], "status": r["status"],
           "status_label": CATALOG_STATUS_LABELS.get(r["status"]), "name": r["name"], "description": r["description"],
           "kind": r["kind"], "kind_label": KIND_LABELS.get(r["kind"]), "construction_notes": r["construction_notes"],
           "holdings_as_of": _iso(r["holdings_as_of"]), "fee_bps": _num(r["fee_bps"]),
           "provider_legal_name": r["provider_legal_name"], "weights_sha256": r["weights_sha256"],
           "positions": len(_loads(r["weights"]) or []), "published_at": _iso(r["published_at"]),
           "retired_at": _iso(r["retired_at"])}
    if not tenant:
        out.update({"model_id": str(r["model_id"]), "model_version_id": str(r["model_version_id"]),
                    "requested_by": str(r["requested_by"]), "checker_id": _s(r["checker_id"]),
                    "approval_task_id": _s(r["approval_task_id"]), "decision_note": r["decision_note"]})
    if include_weights:
        out["weights"] = _loads(r["weights"]) or []
    return out


async def publishing(conn, provider_org) -> dict:
    """The provider's own view: its models, which are publishable, and every catalog version."""
    models = await M.list_models(conn, provider_org)
    cms = {str(r["model_id"]): dict(r) for r in await conn.fetch(
        f"SELECT * FROM {T_CM} WHERE provider_org_id = $1::uuid AND system_to IS NULL", str(provider_org))}
    cvs = [dict(r) for r in await conn.fetch(
        f"SELECT * FROM {T_CV} WHERE provider_org_id = $1::uuid AND system_to IS NULL ORDER BY catalog_model_id, "
        "catalog_version_no", str(provider_org))]
    out = []
    for m in models:
        cm = cms.get(str(m["id"]))
        out.append({"model": {"id": str(m["id"]), "name": m["name"], "source": m["source"], "status": m["status"],
                              "live_version_no": m.get("live_version_no")},
                    "catalog_model": catalog_model_out(cm) if cm else None,
                    "catalog_versions": [catalog_version_out(v) for v in cvs
                                         if cm and str(v["catalog_model_id"]) == str(cm["id"])]})
    return {"models": out}


# ═══ The catalog (tenant) ═══════════════════════════════════════════════════════════════════════════════════════
async def subscription_row(conn, subscription_id, *, org_id=None, provider_org=None, lock: bool = False) -> dict:
    sid = _uuid(subscription_id, "subscription")
    r = await conn.fetchrow(f"SELECT * FROM {T_SUB} WHERE id = $1::uuid AND system_to IS NULL "
                            f"{'FOR UPDATE' if lock else ''}", sid)
    if r is None or (org_id is not None and str(r["org_id"]) != str(org_id)) or (
            provider_org is not None and str(r["provider_org_id"]) != str(provider_org)):
        raise ProviderNotFound("subscription not found")
    return dict(r)


async def latest_published(conn, catalog_model_id) -> dict | None:
    r = await conn.fetchrow(f"SELECT * FROM {T_CV} WHERE catalog_model_id = $1::uuid AND status = 'published' "
                            "AND system_to IS NULL ORDER BY catalog_version_no DESC LIMIT 1", str(catalog_model_id))
    return dict(r) if r else None


async def catalog(conn, org_id) -> list[dict]:
    """Published (and retired) catalog versions the tenant may see — RLS decides (gate open, same kind of org)."""
    rows = [dict(r) for r in await conn.fetch(
        f"SELECT * FROM {T_CV} WHERE status IN ('published', 'retired') AND system_to IS NULL "
        "AND provider_org_id <> $1::uuid ORDER BY catalog_model_id, catalog_version_no", str(org_id))]
    subs = {str(r["catalog_model_id"]): dict(r) for r in await conn.fetch(
        f"SELECT * FROM {T_SUB} WHERE org_id = $1::uuid AND system_to IS NULL AND status IN ('requested', 'active', "
        "'suspended') ORDER BY requested_at", str(org_id))}
    by: dict[str, list] = {}
    for r in rows:
        by.setdefault(str(r["catalog_model_id"]), []).append(r)
    out = []
    for cmid, vs in by.items():
        pub = [v for v in vs if v["status"] == "published"]
        latest = (pub or vs)[-1]
        s = subs.get(cmid)
        out.append({"catalog_model_id": cmid, "name": latest["name"], "description": latest["description"],
                    "kind": latest["kind"], "kind_label": KIND_LABELS.get(latest["kind"]),
                    "provider": latest["provider_legal_name"], "provided_by_label":
                        f"Provided by {latest['provider_legal_name']}", "fee_bps": _num(latest["fee_bps"]),
                    "subscribable": bool(pub), "versions": [catalog_version_out(v, tenant=True) for v in vs],
                    "subscription": subscription_out(s) if s else None})
    return sorted(out, key=lambda c: c["name"].lower())


async def _closed_or_missing(conn, catalog_model_id) -> Exception:
    """A catalog model the tenant cannot see: the gate's refusal if its provider's gate is closed (the provider is
    named only through portfolio.catalog_model_provider), else not found."""
    prov = await conn.fetchval("SELECT portfolio.catalog_model_provider($1::uuid)", str(catalog_model_id))
    if prov is not None and not await is_open(conn, prov):
        return GateClosed(GATE_REFUSAL)
    return ProviderNotFound("catalog model not found")


async def agreement_state(conn, *, org_id, user_id, catalog_model_id) -> dict:
    st = await DL.ack_state(conn, org_id=org_id, user_id=user_id, key=KEY_AGREEMENT,
                            subject=DL.Subject(ACK_SUBJECT, subject_id=str(catalog_model_id)))
    res = st["resolved"]
    return {"agreement": DL.public(res), "in_force": res.get("source") == "library",
            "scope": res.get("scope"), "acknowledged": st["acknowledged"], "current": bool(st["current"])
            and res.get("source") == "library"}


async def acknowledge_agreement(pool, *, org_id, actor_id, catalog_model_id, version_id) -> dict:
    async with pool.acquire() as conn:
        await HT.assert_human(conn, actor_id, org_id)
        cm = await conn.fetchrow(f"SELECT id, provider_org_id FROM {T_CM} WHERE id = $1::uuid AND system_to IS NULL",
                                 _uuid(catalog_model_id, "catalog model"))
        if cm is None:
            raise await _closed_or_missing(conn, _uuid(catalog_model_id, "catalog model"))
        await require_open(conn, cm["provider_org_id"])
        if await latest_published(conn, cm["id"]) is None:
            raise ProviderNotFound("catalog model not found")
        res = await DL.resolve(conn, KEY_AGREEMENT, org_id=org_id)
        if res["source"] != "library":
            raise ProviderConflict("no sub-advisory agreement is in force for this org — the platform must publish one "
                                   f"(library key {KEY_AGREEMENT}) before anyone can subscribe")
        if str(version_id) != res["version_id"]:
            raise ProviderConflict(f"the agreement in force is version {res['version_no']} — read it and acknowledge "
                                   "that version")
        ack = await DL.acknowledge(conn, org_id=org_id, user_id=actor_id, resolved=res,
                                   subject=DL.Subject(ACK_SUBJECT, subject_id=str(cm["id"])),
                                   screen="Replication — Model catalog — subscribe")
    return {"acknowledgement_id": str(ack["id"]), "version_id": res["version_id"], "scope": res["scope"],
            "content_sha256": res["content_sha256"]}


async def request_subscription(pool, *, org_id, actor_id, catalog_model_id) -> dict:
    """The tenant's MAKER (an org admin): refused without an acknowledgement of the agreement version in force for
    THIS tenant; opens the approval task in the PROVIDER org (a platform Super Admin decides)."""
    async with pool.acquire() as conn:
        actor = await HT.assert_human(conn, actor_id, org_id)
        cm = await conn.fetchrow(f"SELECT * FROM {T_CM} WHERE id = $1::uuid AND system_to IS NULL",
                                 _uuid(catalog_model_id, "catalog model"))
        if cm is None:
            raise await _closed_or_missing(conn, _uuid(catalog_model_id, "catalog model"))
        cm = dict(cm)
        provider_org = cm["provider_org_id"]
        await require_open(conn, provider_org)
        if not await conn.fetchval("SELECT portfolio.provider_tenant_allowed($1::uuid, $2::uuid)", str(provider_org),
                                   str(org_id)):
            raise ProviderPermissionError("this org may not subscribe to that provider")
        latest = await latest_published(conn, cm["id"])
        if latest is None or cm["status"] != "publishable":
            raise ProviderConflict("this model has no published version to subscribe to")
        if await conn.fetchval(f"SELECT 1 FROM {T_SUB} WHERE org_id = $1::uuid AND catalog_model_id = $2 AND "
                               "status IN ('requested', 'active', 'suspended') AND system_to IS NULL", str(org_id),
                               cm["id"]):
            raise ProviderConflict("this org already has a subscription (or a request) for this model")
        subject = DL.Subject(ACK_SUBJECT, subject_id=str(cm["id"]))
        st = await DL.ack_state(conn, org_id=org_id, user_id=actor_id, key=KEY_AGREEMENT, subject=subject)
        if not st["required"]:
            raise ProviderConflict("no sub-advisory agreement is in force for this org — the platform must publish one "
                                   f"(library key {KEY_AGREEMENT}) before anyone can subscribe")
        await DL.require_current(conn, org_id=org_id, user_id=actor_id, key=KEY_AGREEMENT, subject=subject)
        res = st["resolved"]
        ack = await DL.latest_ack(conn, org_id=org_id, user_id=actor_id, disclosure_id=res["disclosure_id"],
                                  subject=subject)
        tenant_name = await org_name(conn, org_id)
    sid = str(uuid.uuid4())
    async with as_org(provider_org, False):
        task = await HT.open_task(pool, org_id=provider_org, kind=TASK_SUB, subject_type="provider_subscription",
                                  subject_id=sid, maker_id=None, eligible_permission=PERM_APPROVE_SUB,
                                  title=f"Subscription request — {tenant_name} — {latest['name']}",
                                  payload={"tenant_org_id": str(org_id), "tenant_name": tenant_name,
                                           "catalog_model_id": str(cm["id"]), "model_name": latest["name"],
                                           "agreement_version_no": res["version_no"],
                                           "agreement_scope": res["scope"]},
                                  action_path="/portfolio/replication/provider", actor_kind="system")
    async with pool.acquire() as conn:
        row = dict(await conn.fetchrow(
            f"""INSERT INTO {T_SUB} (id, org_id, provider_org_id, catalog_model_id, status, agreement_version_id,
                    agreement_sha256, agreement_scope, acknowledgement_id, requested_by, approval_task_id)
                VALUES ($1::uuid, $2::uuid, $3::uuid, $4, 'requested', $5::uuid, $6, $7, $8, $9::uuid, $10::uuid)
                RETURNING *""", sid, str(org_id), str(provider_org), cm["id"], res["version_id"],
            res["content_sha256"], res["scope"], ack["id"], str(actor["id"]), task["id"]))
        await _log(conn, org_id=org_id, provider_org_id=provider_org, tenant_org_id=org_id,
                   action="subscription_requested", actor_id=actor["id"], subject_type="subscription", subject_id=sid,
                   detail={"catalog_model_id": str(cm["id"]), "agreement_version_id": res["version_id"],
                           "agreement_scope": res["scope"], "task_id": task["id"]})
    return subscription_out(row)


async def _sub_precheck(pool, task, outcome, actor_id) -> None:
    async with pool.acquire() as conn:
        sub = await conn.fetchrow(f"SELECT * FROM {T_SUB} WHERE id = $1::uuid AND system_to IS NULL",
                                  task["subject_id"])
        if sub is None:
            raise HT.HumanTaskConflict("the subscription behind this task is missing")
        await _assert_super_admin(conn, actor_id)
        if str(sub["requested_by"]) == str(actor_id):
            raise HT.MakerCheckerError("maker-checker: you requested this subscription — a different person approves it")
        if outcome == "approve":
            if not await is_open(conn, sub["provider_org_id"]):
                raise HT.HumanTaskConflict(GATE_REFUSAL)
    if outcome == "approve":
        async with as_org(sub["org_id"], False):
            async with pool.acquire() as conn:
                res = await DL.resolve(conn, KEY_AGREEMENT, org_id=sub["org_id"])
        if res["source"] != "library" or res["version_id"] != str(sub["agreement_version_id"]):
            raise HT.HumanTaskConflict("the sub-advisory agreement in force for this tenant changed since it was "
                                       "acknowledged — the tenant must acknowledge the version in force and request "
                                       "again")


async def _apply_subscription(pool, task, outcome, actor_id, note) -> dict | None:
    provider_org = task["org_id"]
    async with as_org(provider_org, True):
        async with pool.acquire() as conn:
            sub = await conn.fetchrow(f"SELECT * FROM {T_SUB} WHERE id = $1::uuid AND system_to IS NULL FOR UPDATE",
                                      task["subject_id"])
            if sub is None or sub["status"] != "requested" or str(sub["approval_task_id"]) != str(task["id"]):
                return subscription_out(dict(sub)) if sub else None
            sub = dict(sub)
            now = datetime.now(timezone.utc)
            if outcome != "approve":
                row = await _update(conn, T_SUB, sub["id"], {"status": "rejected", "decision_note": note})
                for org in (provider_org, sub["org_id"]):
                    await _log(conn, org_id=org, provider_org_id=provider_org, tenant_org_id=sub["org_id"],
                               action="subscription_rejected", actor_id=actor_id, subject_type="subscription",
                               subject_id=sub["id"], detail={"note": note})
                return subscription_out(row)
            latest = await latest_published(conn, sub["catalog_model_id"])
            if latest is None:
                raise ProviderConflict("the model has no published version any more")
            mirror_id = await conn.fetchval(
                f"""INSERT INTO {M.T_MODEL} (org_id, name, description, source, status, provider_name,
                        provided_subscription_id, kind) VALUES ($1::uuid, $2, $3, 'provided', 'draft', $4, $5, 'long_only')
                    RETURNING id""", str(sub["org_id"]), latest["name"], latest["description"],
                latest["provider_legal_name"], sub["id"])
            await M._event(conn, org_id=sub["org_id"], action="provided_link", actor_id=None, model_id=mirror_id,
                           detail={"subscription_id": str(sub["id"]), "provider": latest["provider_legal_name"]})
            row = await _update(conn, T_SUB, sub["id"], {"status": "active", "approved_by": uuid.UUID(str(actor_id)),
                                                         "approved_at": now, "decision_note": note,
                                                         "started_on": await db_today(conn), "mirror_model_id": mirror_id})
            for org in (provider_org, sub["org_id"]):
                await _log(conn, org_id=org, provider_org_id=provider_org, tenant_org_id=sub["org_id"],
                           action="subscription_approved", actor_id=actor_id, subject_type="subscription",
                           subject_id=sub["id"], detail={"mirror_model_id": str(mirror_id), "note": note})
    out = subscription_out(row)
    out["first_version"] = await import_version(pool, sub=row, cv=latest)
    return out


async def decide_subscription(pool, *, provider_org, actor_id, subscription_id, outcome: str,
                              note: str | None = None) -> dict:
    if outcome not in ("approve", "reject"):
        raise ProviderError("outcome is approve or reject")
    async with pool.acquire() as conn:
        await _assert_super_admin(conn, actor_id)
        sub = await subscription_row(conn, subscription_id, provider_org=provider_org)
        if sub["status"] != "requested":
            raise ProviderConflict(f"this subscription is {sub['status']}, not awaiting approval")
        if str(sub["requested_by"]) == str(actor_id):
            raise MakerCheckerViolation("maker-checker: you requested this subscription — a different person approves "
                                        "it")
        if outcome == "approve":
            await require_open(conn, provider_org)
    try:
        done = await HT.complete_task(pool, org_id=provider_org, task_id=sub["approval_task_id"], actor_id=actor_id,
                                      outcome=outcome, note=note)
    except HT.MakerCheckerError as exc:
        raise MakerCheckerViolation(str(exc)) from exc
    except HT.HumanTaskPermissionError as exc:
        raise ProviderPermissionError(str(exc)) from exc
    except HT.HumanTaskConflict as exc:
        if GATE_REFUSAL in str(exc):
            raise GateClosed(GATE_REFUSAL) from exc
        raise ProviderConflict(str(exc)) from exc
    return done.get("subject_result") or {}


def subscription_out(r: dict) -> dict:
    return {"id": str(r["id"]), "catalog_model_id": str(r["catalog_model_id"]), "status": r["status"],
            "status_label": SUBSCRIPTION_STATUS_LABELS.get(r["status"]), "mirror_model_id": _s(r["mirror_model_id"]),
            "agreement_version_id": str(r["agreement_version_id"]), "agreement_scope": r["agreement_scope"],
            "requested_by": str(r["requested_by"]), "requested_at": _iso(r["requested_at"]),
            "approved_by": _s(r["approved_by"]), "approved_at": _iso(r["approved_at"]),
            "started_on": _iso(r["started_on"]), "suspended_at": _iso(r["suspended_at"]),
            "suspend_reason": r["suspend_reason"], "ended_on": _iso(r["ended_on"]), "end_reason": r["end_reason"],
            "decision_note": r["decision_note"]}


# ═══ Import, adoption, copy (tenant) ════════════════════════════════════════════════════════════════════════════
def _positions_from(weights: list[dict]) -> list[dict]:
    out = []
    for w in weights:
        out.append({c: None for c in M.POS_COLS} | {
            "row_num": int(w["row"]), "global_security_id": w["global_security_id"], "raw_ticker": w.get("ticker"),
            "raw_name": w.get("name"), "target_weight": Decimal(w["weight"]), "alternates": [],
            "resolution_state": "cash" if w.get("cash") else "resolved"})
    return out


async def _cv_of_version(conn, model_version_id) -> dict | None:
    """The catalog version a mirror version was imported from (origin_ref = the provider's model version)."""
    r = await conn.fetchrow(f"SELECT * FROM {T_CV} WHERE model_version_id = $1::uuid AND system_to IS NULL "
                            "AND status IN ('published', 'retired')", str(model_version_id))
    return dict(r) if r else None


async def mirror_on(conn, model_id) -> tuple[dict | None, dict | None]:
    """(the mirror's live approved version, the catalog version it came from)."""
    live = await M.live_approved(conn, model_id)
    return live, (await _cv_of_version(conn, live["origin_ref"]) if live and live.get("origin_ref") else None)


async def import_version(pool, *, sub: dict, cv: dict) -> dict:
    """A published catalog version → a mirror DRAFT in the tenant (read-only import path) + a 'new version
    available' task with the exact weight diff. A suspended / ended subscription receives nothing."""
    tenant = str(sub["org_id"])
    if sub["status"] != "active" or not sub.get("mirror_model_id"):
        return {"subscription_id": str(sub["id"]), "skipped": f"subscription {sub['status']}"}
    async with as_org(tenant, False):
        async with pool.acquire() as conn:
            model = await M.load_model(conn, tenant, sub["mirror_model_id"])
            if await conn.fetchval(f"SELECT 1 FROM {M.T_VER} WHERE model_id = $1 AND origin_ref = $2::uuid AND "
                                   "system_to IS NULL AND status <> 'abandoned'", model["id"],
                                   str(cv["model_version_id"])):
                return {"subscription_id": str(sub["id"]), "skipped": "already imported"}
            open_v = await M.open_version(conn, model["id"], lock=True)
            if open_v is not None and open_v["status"] == "submitted":
                await _log(conn, org_id=tenant, provider_org_id=sub["provider_org_id"], tenant_org_id=tenant,
                           action="version_available", actor_id=None, subject_type="model", subject_id=model["id"],
                           detail={"skipped": "an adoption is awaiting approval — refresh after it is decided",
                                   "catalog_version_id": str(cv["id"])})
                return {"subscription_id": str(sub["id"]), "skipped": "an adoption is awaiting approval"}
            if open_v is not None:          # an older provided draft nobody adopted: superseded by the newer one
                await M._update(conn, M.T_VER, open_v["id"], {
                    "status": "abandoned", "abandoned_at": datetime.now(timezone.utc),
                    "abandon_reason": f"superseded by provider catalog version {cv['catalog_version_no']}"})
            _live, on_cv = await mirror_on(conn, model["id"])
            diff = weight_diff(_loads(on_cv["weights"]) if on_cv else None, _loads(cv["weights"]))
            await conn.execute("SELECT set_config('models.provided_import', 'on', true)")
            version = await M._create_version(
                conn, org_id=tenant, model=model, origin="subscription", maker_ids=[], created_by=None,
                positions=_positions_from(_loads(cv["weights"])), origin_ref=cv["model_version_id"],
                notes=f"Provider catalog version {cv['catalog_version_no']} ({cv['provider_legal_name']})",
                tradability=await M.U.tradability(conn, tenant))
            await conn.execute("SELECT set_config('models.provided_import', '', true)")
            ref = await M._event(conn, org_id=tenant, action="provided_version_draft", actor_id=None,
                                 model_id=model["id"], version_id=version["id"],
                                 detail={"catalog_version_id": str(cv["id"]),
                                         "catalog_version_no": cv["catalog_version_no"]})
            await _log(conn, org_id=tenant, provider_org_id=sub["provider_org_id"], tenant_org_id=tenant,
                       action="version_available", actor_id=None, subject_type="model", subject_id=model["id"],
                       detail={"catalog_version_id": str(cv["id"]), "version_id": str(version["id"]),
                               "changes": len(diff)})
            pre = await _preapproval(conn, tenant)
        await M.publish(pool, [ref])
        task = await HT.open_task(
            pool, org_id=tenant, kind=TASK_AVAILABLE, subject_type=M.TASK_SUBJECT, subject_id=version["id"],
            maker_id=None, eligible_permission=MANAGE,
            title=f"New version available — {model['name']} (provider version {cv['catalog_version_no']})",
            payload={"model_id": str(model["id"]), "version_id": str(version["id"]),
                     "catalog_version_id": str(cv["id"]), "catalog_version_no": cv["catalog_version_no"],
                     "provider": cv["provider_legal_name"], "diff": diff,
                     "from_catalog_version_no": on_cv["catalog_version_no"] if on_cv else None},
            action_path="/portfolio/replication/catalog", actor_kind="system")
        out = {"subscription_id": str(sub["id"]), "model_id": str(model["id"]), "version_id": str(version["id"]),
               "task_id": task["id"], "diff": diff}
        if pre:
            out["preapproved"] = await _preapprove(pool, tenant=tenant, sub=sub, model=model, version=version,
                                                   approver=pre, available_task=task)
    return out


async def _preapproval(conn, tenant) -> str | None:
    """The org setting (default false) and WHO recorded it — that person is the standing approver."""
    r = await conn.fetchrow("SELECT setting_value, updated_by FROM org_settings WHERE org_id = $1::uuid AND "
                            "setting_key = $2", str(tenant), PREAPPROVE_SETTING)
    if r is None or r["updated_by"] is None:
        return None
    v = _loads(r["setting_value"])
    return str(r["updated_by"]) if v is True or (isinstance(v, dict) and v.get("value") is True) else None


async def _preapprove(pool, *, tenant, sub, model, version, approver: str, available_task: dict) -> dict:
    try:
        sub_v = await M._submit_system(pool, org_id=tenant, model=model, version=version,
                                       why="a provided model's new version (adoption pre-approved by the org setting)")
        done = await HT.complete_task(pool, org_id=tenant, task_id=sub_v["task_id"], actor_id=approver,
                                      outcome="approve",
                                      note=f"pre-approved by the org setting {PREAPPROVE_SETTING} recorded by this "
                                           "person")
        await HT.complete_task(pool, org_id=tenant, task_id=available_task["id"], actor_id=approver, outcome="done",
                               note="adopted under the pre-approval setting")
        async with pool.acquire() as conn:
            await _log(conn, org_id=tenant, provider_org_id=sub["provider_org_id"], tenant_org_id=tenant,
                       action="adoption_preapproved", actor_id=approver, subject_type="model",
                       subject_id=model["id"], detail={"version_id": str(version["id"])})
        return {"approved": True, "approver": approver, "status": (done.get("subject_result") or {}).get("status")}
    except Exception as exc:  # noqa: BLE001 — the version stays for a person to decide
        log.warning("pre-approved adoption of %s failed: %s", version["id"], exc)
        return {"approved": False, "reason": str(exc)}


async def fan_out(pool, cv: dict) -> list[dict]:
    """A newly published version → every ACTIVE subscription of its model (cross-org read, platform scope)."""
    async with as_org(cv["provider_org_id"], True):
        async with pool.acquire() as conn:
            subs = [dict(r) for r in await conn.fetch(
                f"SELECT * FROM {T_SUB} WHERE catalog_model_id = $1 AND status = 'active' AND system_to IS NULL",
                cv["catalog_model_id"])]
    out = []
    for s in subs:
        try:
            out.append(await import_version(pool, sub=s, cv=cv))
        except Exception as exc:  # noqa: BLE001 — one tenant never stops the others
            log.exception("provided import failed for subscription %s", s["id"])
            out.append({"subscription_id": str(s["id"]), "error": str(exc)})
    return out


async def subscription_for_model(conn, model: dict) -> dict | None:
    if model.get("source") != "provided" or not model.get("provided_subscription_id"):
        return None
    r = await conn.fetchrow(f"SELECT * FROM {T_SUB} WHERE id = $1 AND system_to IS NULL",
                            model["provided_subscription_id"])
    return dict(r) if r else None


async def adoptable_refusal(conn, model: dict) -> str | None:
    """Why a provided model may not take a new adoption / join now (None = it may)."""
    sub = await subscription_for_model(conn, model)
    if sub is None:
        return None if model.get("source") != "provided" else "the subscription behind this model is missing"
    if not await is_open(conn, sub["provider_org_id"]):
        return GATE_REFUSAL
    if sub["status"] != "active":
        return (f"the subscription to {model['name']} is {sub['status']} — no new adoptions or joins; accounts "
                "already on it keep their version")
    return None


async def assert_adoptable(conn, org_id, model: dict) -> None:
    """rep3 / rep5 hook: a provided model takes a new adoption or join only while the gate is open and the
    subscription is active."""
    why = await adoptable_refusal(conn, model)
    if why:
        raise M.ModelConflict(why)


async def _adoption_precheck(pool, task, outcome, actor_id) -> None:
    if outcome != "approve":
        return
    async with pool.acquire() as conn:
        v = await conn.fetchrow(f"SELECT model_id FROM {M.T_VER} WHERE id = $1::uuid AND system_to IS NULL",
                                task["subject_id"])
        if v is None:
            return
        m = await conn.fetchrow(f"SELECT * FROM {M.T_MODEL} WHERE id = $1 AND system_to IS NULL", v["model_id"])
        if m is None or m["source"] != "provided":
            return
        why = await adoptable_refusal(conn, dict(m))
    if why:
        raise HT.HumanTaskConflict(why)


async def adopt(pool, *, org_id, actor_id, subscription_id, version_id) -> dict:
    """The tenant's MAKER submits the provided draft (rep3 submit — maker = the requester); a different holder of
    approve_models approves it through rep3's approval task, which yields rebalance proposals (rep5)."""
    async with pool.acquire() as conn:
        await HT.assert_human(conn, actor_id, org_id)
        sub = await subscription_row(conn, subscription_id, org_id=org_id)
        await require_open(conn, sub["provider_org_id"])
        if sub["status"] != "active":
            raise ProviderConflict(f"the subscription is {sub['status']} — no new adoptions; accounts already on it "
                                   "keep their version")
        model = await M.load_model(conn, org_id, sub["mirror_model_id"])
        v = await M.load_version(conn, org_id, model["id"], version_id)
        if v["status"] != "draft" or v["origin"] != "subscription":
            raise ProviderConflict(f"version {v['version_no']} is {v['status']} — only a provided draft is adopted")
        avail = await HT.list_tasks(conn, org_id, status="open", kind=TASK_AVAILABLE, subject_type=M.TASK_SUBJECT,
                                    subject_id=v["id"], limit=1)
    new = await M.submit(pool, org_id=org_id, actor_id=actor_id, model_id=model["id"], version_id=v["id"])
    if avail:
        try:
            await HT.complete_task(pool, org_id=org_id, task_id=avail[0]["id"], actor_id=actor_id, outcome="done",
                                   note="adoption submitted for approval")
        except HT.HumanTaskError as exc:
            log.warning("could not close the 'new version available' task %s: %s", avail[0]["id"], exc)
    async with pool.acquire() as conn:
        await _log(conn, org_id=org_id, provider_org_id=sub["provider_org_id"], tenant_org_id=org_id,
                   action="adoption_requested", actor_id=actor_id, subject_type="model", subject_id=model["id"],
                   detail={"version_id": str(v["id"]), "approval_task_id": _s(new.get("approval_task_id"))})
    return M.version_out(new)


async def refresh(pool, *, org_id, actor_id, subscription_id) -> dict:
    """Import the latest published version if the mirror does not have it yet (after an in-flight adoption)."""
    async with pool.acquire() as conn:
        await HT.assert_human(conn, actor_id, org_id)
        sub = await subscription_row(conn, subscription_id, org_id=org_id)
        await require_open(conn, sub["provider_org_id"])
        if sub["status"] != "active":
            raise ProviderConflict(f"the subscription is {sub['status']} — no new versions are imported; accounts "
                                   "already on it keep their version")
        latest = await latest_published(conn, sub["catalog_model_id"])     # RLS: published + gate open
    if latest is None:
        raise ProviderConflict("the model has no published version")
    return await import_version(pool, sub=sub, cv=latest)


async def copy_model(pool, *, org_id, actor_id, subscription_id, name: str) -> dict:
    """COPY into the tenant's own registry: a new tenant-owned model ("Holdings of …", source 'provided_copy') —
    no subscription link, so no model-provider fee; its draft goes through rep3's own approval."""
    n = (name or "").strip()
    if not n.lower().startswith(NAME_PREFIX) or len(n) <= len(NAME_PREFIX) or len(n) > 200:
        raise ProviderError('a copy keeps the naming rule: "Holdings of …"')
    async with pool.acquire() as conn:
        actor = await HT.assert_human(conn, actor_id, org_id)
        sub = await subscription_row(conn, subscription_id, org_id=org_id)
        await require_open(conn, sub["provider_org_id"])
        if sub["status"] != "active":
            raise ProviderConflict(f"the subscription is {sub['status']} — a model is copied only from an active one")
        mirror = await M.load_model(conn, org_id, sub["mirror_model_id"])
        _live, cv = await mirror_on(conn, mirror["id"])
        if cv is None:                     # nothing adopted yet: copy the provided draft that has arrived
            pending = await M.open_version(conn, mirror["id"])
            cv = await _cv_of_version(conn, pending["origin_ref"]) if pending and pending.get("origin_ref") else None
        if cv is None:
            raise ProviderConflict("nothing to copy yet — no provided version has arrived")
        if await conn.fetchval(f"SELECT 1 FROM {M.T_MODEL} WHERE org_id = $1::uuid AND lower(btrim(name)) = lower($2) "
                               "AND valid_to IS NULL AND system_to IS NULL", str(org_id), n):
            raise ProviderConflict(f"a model named {n!r} already exists")
        mid = await conn.fetchval(
            f"""INSERT INTO {M.T_MODEL} (org_id, name, description, source, status, copied_from_catalog_version_id,
                    created_by, kind) VALUES ($1::uuid, $2, $3, 'provided_copy', 'draft', $4, $5::uuid, 'long_only')
                RETURNING id""", str(org_id), n,
            f"{cv['description']} (copied from provider catalog version {cv['catalog_version_no']}; no longer "
            "provided)", cv["id"], str(actor["id"]))
        model = dict(await conn.fetchrow(f"SELECT * FROM {M.T_MODEL} WHERE id = $1", mid))
        version = await M._create_version(conn, org_id=org_id, model=model, origin="library_copy",
                                          maker_ids=[actor["id"]], created_by=actor["id"],
                                          positions=_positions_from(_loads(cv["weights"])), origin_ref=None,
                                          notes=f"Copied from provider catalog version {cv['catalog_version_no']}",
                                          tradability=await M.U.tradability(conn, org_id))
        ref = await M._event(conn, org_id=org_id, action="provided_copy", actor_id=actor["id"], model_id=mid,
                             version_id=version["id"], detail={"catalog_version_id": str(cv["id"]), "name": n})
        await _log(conn, org_id=org_id, provider_org_id=sub["provider_org_id"], tenant_org_id=org_id, action="copied",
                   actor_id=actor["id"], subject_type="model", subject_id=mid,
                   detail={"catalog_version_id": str(cv["id"]), "subscription_id": str(sub["id"])})
    await M.publish(pool, [ref])
    return {"model": M.model_out(model), "version": M.version_out(version)}


# ═══ Suspend / resume / end ═════════════════════════════════════════════════════════════════════════════════════
async def _open_move_tasks(pool, *, sub: dict, why: str) -> list[dict]:
    """Every account still following the mirror gets a task to move (it keeps its version meanwhile)."""
    tenant = str(sub["org_id"])
    out = []
    async with as_org(tenant, False):
        async with pool.acquire() as conn:
            links = [dict(r) for r in await conn.fetch(
                "SELECT l.id, l.account_id, a.account_number_masked FROM public.account_model_links l "
                "JOIN public.accounts a ON a.id = l.account_id WHERE l.org_id = $1::uuid AND l.model_id = $2 "
                "AND l.status = 'active' AND l.valid_to IS NULL AND l.system_to IS NULL", tenant,
                sub["mirror_model_id"])] if sub.get("mirror_model_id") else []
            model = await conn.fetchrow(f"SELECT name FROM {M.T_MODEL} WHERE id = $1 AND system_to IS NULL",
                                        sub["mirror_model_id"]) if sub.get("mirror_model_id") else None
        for ln in links:
            try:
                t = await HT.open_task(pool, org_id=tenant, kind=TASK_MOVE, subject_type="account_model_link",
                                       subject_id=ln["id"], maker_id=None, eligible_permission=MOVE_PERMISSION,
                                       title=f"Move account {ln['account_number_masked']} off "
                                             f"{model['name'] if model else 'a provided model'}",
                                       payload={"account_id": str(ln["account_id"]), "link_id": str(ln["id"]),
                                                "model_id": _s(sub.get("mirror_model_id")), "why": why},
                                       action_path=f"/portfolio/accounts/{ln['account_id']}", actor_kind="system")
                out.append({"link_id": str(ln["id"]), "task_id": t["id"]})
            except HT.HumanTaskConflict:
                out.append({"link_id": str(ln["id"]), "skipped": "a move task is already open"})
        if links:
            async with pool.acquire() as conn:
                await _log(conn, org_id=tenant, provider_org_id=sub["provider_org_id"], tenant_org_id=tenant,
                           action="move_task_opened", actor_id=None, subject_type="subscription",
                           subject_id=sub["id"], detail={"accounts": len(links), "why": why})
    return out


async def change_subscription(pool, *, acting_org, actor_id, subscription_id, action: str, reason: str,
                              is_super_admin: bool) -> dict:
    """suspend / resume — a platform Super Admin (acting in the provider org); end — that Super Admin or the
    tenant's org admin (acting in the tenant org). Every change is logged in BOTH orgs."""
    if action not in ("suspend", "resume", "end"):
        raise ProviderError("action is suspend, resume or end")
    if not (reason or "").strip():
        raise ProviderError("a reason is required")
    # read in the CALLER's own context: a Super Admin sees every row, a tenant only its own (RLS) — no escalation
    async with pool.acquire() as conn:
        sub = await subscription_row(conn, subscription_id)
        on = await db_today(conn)
        if is_super_admin:
            await _assert_super_admin(conn, actor_id)
    side = "provider" if str(sub["provider_org_id"]) == str(acting_org) else \
        "tenant" if str(sub["org_id"]) == str(acting_org) else None
    if side is None:
        raise ProviderNotFound("subscription not found")
    if side == "provider" and not is_super_admin:
        raise ProviderPermissionError("Super Admin required on the platform side")
    if side == "tenant" and action != "end":
        raise ProviderPermissionError("a tenant ends its own subscription; suspending and resuming are the "
                                      "platform's")
    edges = {"suspend": ("active",), "resume": ("suspended",), "end": ("requested", "active", "suspended")}
    if sub["status"] not in edges[action]:
        raise ProviderConflict(f"this subscription is {sub['status']} — it cannot be {action}ed")
    now = datetime.now(timezone.utc)
    fields = {"suspend": {"status": "suspended", "suspended_by": uuid.UUID(str(actor_id)), "suspended_at": now,
                          "suspend_reason": reason.strip()},
              "resume": {"status": "active"},
              "end": {"status": "ended", "ended_by": uuid.UUID(str(actor_id)), "ended_at": now,
                      "ended_on": on, "end_reason": reason.strip()}}[action]
    act = {"suspend": "subscription_suspended", "resume": "subscription_resumed", "end": "subscription_ended"}[action]
    detail = {"reason": reason.strip(), "by": side}
    async with pool.acquire() as conn:
        if action == "resume":
            await require_open(conn, sub["provider_org_id"])
        cur = await subscription_row(conn, sub["id"], lock=True)
        if cur["status"] != sub["status"]:
            raise ProviderConflict("the subscription changed meanwhile — reload it")
        row = await _update(conn, T_SUB, sub["id"], fields)
        await _log(conn, org_id=acting_org, provider_org_id=sub["provider_org_id"], tenant_org_id=sub["org_id"],
                   action=act, actor_id=actor_id, subject_type="subscription", subject_id=sub["id"], detail=detail)
    other = sub["org_id"] if side == "provider" else sub["provider_org_id"]
    async with as_org(other, False):         # the counterpart's own log row, written in ITS context
        async with pool.acquire() as conn:
            await _log(conn, org_id=other, provider_org_id=sub["provider_org_id"], tenant_org_id=sub["org_id"],
                       action=act, actor_id=actor_id, subject_type="subscription", subject_id=sub["id"],
                       detail=detail)
    out = subscription_out(row)
    if action in ("suspend", "end") and sub["status"] != "requested":
        out["move_tasks"] = await _open_move_tasks(pool, sub=row, why=f"subscription {row['status']}: "
                                                                        f"{reason.strip()}")
    if action == "end" and sub["status"] == "requested" and sub.get("approval_task_id"):
        out["note"] = "the platform's approval task now refuses (the request is ended)"
    return out


async def tenant_subscriptions(conn, org_id) -> list[dict]:
    rows = [dict(r) for r in await conn.fetch(
        f"SELECT * FROM {T_SUB} WHERE org_id = $1::uuid AND system_to IS NULL ORDER BY requested_at DESC", str(org_id))]
    out = []
    for r in rows:
        d = subscription_out(r)
        if r["mirror_model_id"]:
            m = await conn.fetchrow(f"SELECT id, name, provider_name, status FROM {M.T_MODEL} WHERE id = $1 "
                                    "AND system_to IS NULL", r["mirror_model_id"])
            live, cv = await mirror_on(conn, r["mirror_model_id"])
            drafts = [dict(v) for v in await conn.fetch(
                f"SELECT id, version_no, status, origin_ref FROM {M.T_VER} WHERE model_id = $1 AND status IN "
                "('draft', 'submitted') AND system_to IS NULL", r["mirror_model_id"])]
            d["model"] = {"id": str(m["id"]), "name": m["name"], "provided_by": m["provider_name"],
                          "provided_by_label": f"Provided by {m['provider_name']}", "read_only": True,
                          "status": m["status"], "live_version_no": live["version_no"] if live else None,
                          "on_catalog_version_no": cv["catalog_version_no"] if cv else None,
                          "pending": [{"version_id": str(v["id"]), "version_no": v["version_no"],
                                       "status": v["status"]} for v in drafts]} if m else None
            d["accounts"] = int(await conn.fetchval(
                "SELECT count(DISTINCT account_id) FROM public.account_model_links WHERE org_id = $1::uuid AND "
                "model_id = $2 AND status = 'active' AND valid_to IS NULL AND system_to IS NULL", str(org_id),
                r["mirror_model_id"]))
        d["tenant_adv_obligation"] = OPERATOR_ITEMS[4]
        out.append(d)
    return out


# ═══ What the provider sees of its tenants — status, counts, assets ═══════════════════════════════════════════
async def subscribers(conn, provider_org, day: date | None = None) -> dict:
    on = day or await db_today(conn)
    status = [dict(r) for r in await conn.fetch("SELECT * FROM portfolio.provider_subscription_status($1::uuid)",
                                                str(provider_org))]
    usage = [dict(r) for r in await conn.fetch("SELECT * FROM portfolio.provider_model_usage($1::uuid, $2::date)",
                                               str(provider_org), on)]
    return {"as_of": on.isoformat(),
            "subscriptions": [{"subscription_id": str(r["subscription_id"]), "tenant_org_id": str(r["tenant_org_id"]),
                               "tenant_name": r["tenant_name"], "catalog_model_id": str(r["catalog_model_id"]),
                               "status": r["status"], "status_label": SUBSCRIPTION_STATUS_LABELS.get(r["status"]),
                               "requested_at": _iso(r["requested_at"]), "started_on": _iso(r["started_on"]),
                               "ended_on": _iso(r["ended_on"]), "suspended_at": _iso(r["suspended_at"])}
                              for r in status],
            "usage": [{"tenant_org_id": str(r["tenant_org_id"]), "subscription_id": str(r["subscription_id"]),
                       "catalog_model_id": str(r["catalog_model_id"]), "catalog_version_id": str(r["catalog_version_id"]),
                       "account_count": int(r["account_count"]), "model_assets": _num(cents(Decimal(r["model_assets"])))}
                      for r in usage]}


# ═══ The fee ════════════════════════════════════════════════════════════════════════════════════════════════════
async def accrue(pool, *, provider_org, actor_id, day_from: date, day_to: date) -> dict:
    """One accrual per (subscription, catalog version, day) from portfolio.provider_model_usage — restated (valid
    axis: closed + successor) when the day's assets changed. Platform scope (Super Admin context)."""
    if day_to < day_from:
        raise ProviderError("the period ends before it starts")
    if (day_to - day_from).days + 1 > MAX_ACCRUAL_DAYS:
        raise ProviderError(f"accrue at most {MAX_ACCRUAL_DAYS} days at a time")
    written, restated, unchanged = [], [], 0
    async with as_org(provider_org, True):
        async with pool.acquire() as conn:
            await require_open(conn, provider_org)
            if day_to > await db_today(conn):
                raise ProviderError("a future day cannot be accrued")
            d = day_from
            while d <= day_to:
                for u in await conn.fetch("SELECT * FROM portfolio.provider_model_usage($1::uuid, $2::date)",
                                          str(provider_org), d):
                    cv = await conn.fetchrow(f"SELECT fee_bps FROM {T_CV} WHERE id = $1 AND system_to IS NULL",
                                             u["catalog_version_id"])
                    bps = Decimal(cv["fee_bps"])
                    assets = cents(Decimal(u["model_assets"]))
                    cur = await conn.fetchrow(
                        f"SELECT * FROM {T_ACC} WHERE subscription_id = $1 AND catalog_version_id = $2 AND "
                        "accrual_date = $3 AND valid_to IS NULL AND system_to IS NULL FOR UPDATE",
                        u["subscription_id"], u["catalog_version_id"], d)
                    if cur is not None and Decimal(cur["model_assets"]) == assets and \
                            cur["account_count"] == u["account_count"] and Decimal(cur["fee_bps"]) == bps:
                        unchanged += 1
                        continue
                    if cur is not None:
                        await conn.execute(f"UPDATE {T_ACC} SET valid_to = now() WHERE id = $1", cur["id"])
                    aid = await conn.fetchval(
                        f"""INSERT INTO {T_ACC} (provider_org_id, tenant_org_id, subscription_id, catalog_model_id,
                                catalog_version_id, accrual_date, account_count, model_assets, fee_bps, day_basis,
                                daily_fee, supersedes_id, correction_reason, computed_by)
                            VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14::uuid)
                            RETURNING id""", str(provider_org), u["tenant_org_id"], u["subscription_id"],
                        u["catalog_model_id"], u["catalog_version_id"], d, u["account_count"], assets, bps, DAY_BASIS,
                        daily_fee(assets, bps), cur["id"] if cur else None,
                        "restated: the day's model assets changed" if cur else None, _s(actor_id))
                    (restated if cur else written).append(str(aid))
                d += timedelta(days=1)
            if written or restated:
                await _log(conn, org_id=provider_org, provider_org_id=provider_org, action="accrued",
                           actor_id=actor_id, subject_type="accrual", subject_id=None,
                           detail={"from": day_from.isoformat(), "to": day_to.isoformat(), "written": len(written),
                                   "restated": len(restated)})
    return {"written": written, "restated": restated, "unchanged": unchanged}


def accrual_out(r: dict) -> dict:
    return {"id": str(r["id"]), "subscription_id": str(r["subscription_id"]), "tenant_org_id": str(r["tenant_org_id"]),
            "catalog_version_id": str(r["catalog_version_id"]), "accrual_date": _iso(r["accrual_date"]),
            "account_count": r["account_count"], "model_assets": _num(r["model_assets"]), "fee_bps": _num(r["fee_bps"]),
            "daily_fee": _num(r["daily_fee"]), "supersedes_id": _s(r["supersedes_id"]),
            "live": r["valid_to"] is None}


async def invoice(pool, *, provider_org, actor_id, subscription_id, period_start: date, period_end: date) -> dict:
    """One invoice per subscription per period: a "Model-provider fee" line per catalog version, posted to the GL
    (Dr receivable / Cr revenue) in the provider record's ledger org through services.ledger.posting."""
    from services.ledger.posting import VEHICLE_KIND_LEDGER_BOOK, build_entry_on_conn, post_on_conn

    if period_end < period_start:
        raise ProviderError("the period ends before it starts")
    days = (period_end - period_start).days + 1
    async with as_org(provider_org, True):
        async with pool.acquire() as conn:
            await _assert_super_admin(conn, actor_id)
            await require_open(conn, provider_org)
            sub = await subscription_row(conn, subscription_id, provider_org=provider_org)
            if await conn.fetchval(
                    f"SELECT 1 FROM {T_INV} i WHERE i.subscription_id = $1 AND i.kind = 'invoice' AND i.period_start "
                    f"<= $3 AND i.period_end >= $2 AND NOT EXISTS (SELECT 1 FROM {T_INV} r WHERE r.reverses_invoice_id "
                    "= i.id)", sub["id"], period_start, period_end):
                raise ProviderConflict("an invoice already covers part of this period — correct it (reversal) first")
            accs = [dict(r) for r in await conn.fetch(
                f"SELECT * FROM {T_ACC} WHERE subscription_id = $1 AND accrual_date BETWEEN $2 AND $3 AND valid_to IS "
                "NULL AND system_to IS NULL ORDER BY catalog_version_id, accrual_date", sub["id"], period_start,
                period_end)]
            if not accs:
                raise ProviderConflict("nothing accrued in this period")
            groups: dict[str, list] = {}
            for a in accs:
                groups.setdefault(str(a["catalog_version_id"]), []).append(a)
            lines = []
            for i, (cvid, rows) in enumerate(sorted(groups.items()), start=1):
                bps = Decimal(rows[0]["fee_bps"])
                if any(Decimal(r["fee_bps"]) != bps for r in rows):
                    raise ProviderConflict("a catalog version's fee changed within the period — split the period")
                total_assets = sum((Decimal(r["model_assets"]) for r in rows), Decimal(0))
                lines.append({"line_no": i, "catalog_version_id": cvid, "catalog_model_id": str(rows[0]["catalog_model_id"]),
                              "fee_bps": bps, "days": days, "sum_daily_assets": total_assets,
                              "average_daily_assets": (total_assets / days).quantize(Decimal("0.000001"),
                                                                                    rounding=ROUND_HALF_UP),
                              "amount": period_amount(total_assets, bps), "accrual_ids": [r["id"] for r in rows]})
            amount = sum((ln["amount"] for ln in lines), Decimal(0))
            if amount <= 0:
                raise ProviderConflict("the period's fee is zero — nothing to invoice")
            record = await provider_row(conn, provider_org)
            book = await ensure_ledger(conn, record["ledger_org_id"])
            inv_id = str(uuid.uuid4())
            tenant_name = await org_name(conn, sub["org_id"])
            entry = await build_entry_on_conn(
                conn, book, TXN_CODE, period_end, amount, {}, created_by=str(actor_id),
                vehicle_kind=VEHICLE_KIND_LEDGER_BOOK,
                memo=f"{LINE_LABEL} — {tenant_name} — {period_start.isoformat()} to {period_end.isoformat()}",
                source_event_id=inv_id)
            await post_on_conn(conn, str(entry["id"]), str(actor_id))
            inv = dict(await conn.fetchrow(
                f"""INSERT INTO {T_INV} (id, provider_org_id, tenant_org_id, subscription_id, ledger_org_id, kind,
                        period_start, period_end, amount, journal_entry_id, issued_by)
                    VALUES ($1::uuid, $2::uuid, $3, $4, $5, 'invoice', $6, $7, $8, $9, $10::uuid) RETURNING *""",
                inv_id, str(provider_org), sub["org_id"], sub["id"], record["ledger_org_id"], period_start, period_end,
                amount, entry["id"], str(actor_id)))
            for ln in lines:
                await conn.execute(
                    f"""INSERT INTO {T_LINE} (invoice_id, provider_org_id, tenant_org_id, line_no, label,
                            catalog_model_id, catalog_version_id, fee_bps, days, sum_daily_assets,
                            average_daily_assets, amount, accrual_ids)
                        VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6::uuid, $7::uuid, $8, $9, $10, $11, $12, $13::uuid[])""",
                    inv_id, str(provider_org), sub["org_id"], ln["line_no"], LINE_LABEL, ln["catalog_model_id"],
                    ln["catalog_version_id"], ln["fee_bps"], ln["days"], ln["sum_daily_assets"],
                    ln["average_daily_assets"], ln["amount"], ln["accrual_ids"])
            await _log(conn, org_id=provider_org, provider_org_id=provider_org, tenant_org_id=sub["org_id"],
                       action="invoiced", actor_id=actor_id, subject_type="invoice", subject_id=inv_id,
                       detail={"amount": str(amount), "journal_entry_id": str(entry["id"]),
                               "period": [period_start.isoformat(), period_end.isoformat()]})
            return await invoice_detail(conn, inv_id)


async def correct_invoice(pool, *, provider_org, actor_id, invoice_id, reason: str) -> dict:
    """A correction is a REVERSAL: fn_reverse_journal_entry mirrors the posted entry, and a reversal invoice (with
    mirrored lines) records it. The original invoice and its lines are never edited. Re-accrue, then re-invoice."""
    if not (reason or "").strip():
        raise ProviderError("a correction needs a reason")
    async with as_org(provider_org, True):
        async with pool.acquire() as conn:
            await _assert_super_admin(conn, actor_id)
            await require_open(conn, provider_org)
            inv = await conn.fetchrow(f"SELECT * FROM {T_INV} WHERE id = $1::uuid AND provider_org_id = $2::uuid",
                                      _uuid(invoice_id, "invoice"), str(provider_org))
            if inv is None:
                raise ProviderNotFound("invoice not found")
            if inv["kind"] != "invoice":
                raise ProviderConflict("a reversal is not itself reversed")
            if await conn.fetchval(f"SELECT 1 FROM {T_INV} WHERE reverses_invoice_id = $1", inv["id"]):
                raise ProviderConflict("this invoice is already reversed")
            rev_entry = await conn.fetchval("SELECT fn_reverse_journal_entry($1::uuid, $2::text, $3::uuid)",
                                            inv["journal_entry_id"], reason.strip(), str(actor_id))
            rid = str(uuid.uuid4())
            await conn.execute(
                f"""INSERT INTO {T_INV} (id, provider_org_id, tenant_org_id, subscription_id, ledger_org_id, kind,
                        period_start, period_end, amount, reverses_invoice_id, journal_entry_id, reason, issued_by)
                    VALUES ($1::uuid, $2, $3, $4, $5, 'reversal', $6, $7, $8, $9, $10, $11, $12::uuid)""",
                rid, inv["provider_org_id"], inv["tenant_org_id"], inv["subscription_id"], inv["ledger_org_id"],
                inv["period_start"], inv["period_end"], inv["amount"], inv["id"], rev_entry, reason.strip(),
                str(actor_id))
            await conn.execute(
                f"""INSERT INTO {T_LINE} (invoice_id, provider_org_id, tenant_org_id, line_no, label, catalog_model_id,
                        catalog_version_id, fee_bps, days, sum_daily_assets, average_daily_assets, amount, accrual_ids)
                    SELECT $1::uuid, provider_org_id, tenant_org_id, line_no, label, catalog_model_id,
                           catalog_version_id, fee_bps, days, sum_daily_assets, average_daily_assets, amount, accrual_ids
                      FROM {T_LINE} WHERE invoice_id = $2""", rid, inv["id"])
            await _log(conn, org_id=provider_org, provider_org_id=provider_org, tenant_org_id=inv["tenant_org_id"],
                       action="invoice_reversed", actor_id=actor_id, subject_type="invoice", subject_id=inv["id"],
                       detail={"reversal_invoice_id": rid, "reversal_journal_entry_id": str(rev_entry),
                               "reason": reason.strip()})
            return await invoice_detail(conn, rid)


async def invoice_detail(conn, invoice_id) -> dict:
    inv = await conn.fetchrow(f"SELECT * FROM {T_INV} WHERE id = $1::uuid", str(invoice_id))
    if inv is None:
        raise ProviderNotFound("invoice not found")
    lines = await conn.fetch(f"SELECT * FROM {T_LINE} WHERE invoice_id = $1 ORDER BY line_no", inv["id"])
    reversed_by = await conn.fetchval(f"SELECT id FROM {T_INV} WHERE reverses_invoice_id = $1", inv["id"])
    return {"id": str(inv["id"]), "kind": inv["kind"], "subscription_id": str(inv["subscription_id"]),
            "tenant_org_id": str(inv["tenant_org_id"]), "provider_org_id": str(inv["provider_org_id"]),
            "ledger_org_id": str(inv["ledger_org_id"]), "period_start": _iso(inv["period_start"]),
            "period_end": _iso(inv["period_end"]), "amount": _num(inv["amount"]),
            "journal_entry_id": str(inv["journal_entry_id"]), "reverses_invoice_id": _s(inv["reverses_invoice_id"]),
            "reversed_by": _s(reversed_by), "reason": inv["reason"], "issued_at": _iso(inv["issued_at"]),
            "lines": [{"line_no": ln["line_no"], "label": ln["label"], "catalog_version_id": str(ln["catalog_version_id"]),
                       "fee_bps": _num(ln["fee_bps"]), "days": ln["days"], "sum_daily_assets": _num(ln["sum_daily_assets"]),
                       "average_daily_assets": _num(ln["average_daily_assets"]), "amount": _num(ln["amount"]),
                       "accruals": len(ln["accrual_ids"])} for ln in lines]}


async def invoices(conn, org_id) -> list[dict]:
    """Every invoice the caller's org is a party to (RLS: provider or tenant)."""
    ids = [r["id"] for r in await conn.fetch(
        f"SELECT id FROM {T_INV} WHERE provider_org_id = $1::uuid OR tenant_org_id = $1::uuid "
        "ORDER BY issued_at DESC LIMIT 200", str(org_id))]
    return [await invoice_detail(conn, i) for i in ids]


# ═══ The member notice on a proposal (Task 8) ═══════════════════════════════════════════════════════════════════
async def proposal_notice(conn, org_id, proposal: dict) -> dict | None:
    """A proposal on a PROVIDED model shows the provider and that the model is provided by a sub-adviser
    (library key model_provider.member_notice, resolved for the proposal's account)."""
    if not proposal.get("model_id"):
        return None
    m = await conn.fetchrow(f"SELECT source, provider_name, name FROM {M.T_MODEL} WHERE id = $1::uuid AND "
                            "system_to IS NULL ORDER BY valid_to NULLS FIRST LIMIT 1", str(proposal["model_id"]))
    if m is None or m["source"] != "provided":
        return None
    provider = m["provider_name"] or "the sub-adviser"
    res = await DL.resolve_safe(conn, KEY_NOTICE, org_id=org_id, account_id=proposal.get("account_id"))
    pub = DL.public(res)
    pub["text"] = (pub.get("text") or MEMBER_NOTICE_FALLBACK).replace("{provider}", provider)
    return {"provided_by": provider, "provided_by_label": f"Provided by {provider}", "sub_adviser": True,
            "model_name": m["name"], "notice": pub, "tenant_adv_obligation": OPERATOR_ITEMS[4]}


# ═══ rep3 platform shelf: the same gate for REAL orgs ══════════════════════════════════════════════════════════
async def platform_shelf_refusal(conn, *, org_id, actor_id, publishing: bool) -> str | None:
    """rep3's platform library shelf publishes to every org. For a REAL org it now needs the platform org's gate
    open; a fixture (is_test) user publishing or a fixture org loading stays allowed, so rep3's own verify still
    runs. None = allowed."""
    if await is_open(conn, PLATFORM_ORG_ID):
        return None
    if publishing:
        ok = await conn.fetchval("SELECT is_test FROM users WHERE id = $1::uuid", str(actor_id))
    else:
        ok = await conn.fetchval("SELECT is_test FROM organizations WHERE id = $1::uuid", str(org_id))
    return None if ok else GATE_REFUSAL


def vocabularies(*, can_write: bool, can_approve: bool, can_admin: bool, is_super_admin: bool) -> dict:
    def pairs(d):
        return [{"key": k, "label": v} for k, v in d.items()]

    return {"registration_statuses": pairs(REGISTRATION_LABELS), "catalog_statuses": pairs(CATALOG_STATUS_LABELS),
            "subscription_statuses": pairs(SUBSCRIPTION_STATUS_LABELS), "kinds": pairs(KIND_LABELS),
            "fee_bps_max": str(FEE_BPS_MAX), "line_label": LINE_LABEL, "refusal": GATE_REFUSAL,
            "editable": (["subscribe", "end"] if can_admin else []) + (["adopt", "copy", "refresh"] if can_write else []),
            "inline_editable": [],
            "platform_editable": (["provider", "gate", "publish", "decide", "retire", "subscriptions", "fees"]
                                  if is_super_admin else [])}


HT.register_kind(TASK_PUBLISH, label="Catalog publication", outcomes=("approve", "reject"), maker_checker=True,
                 on_complete=_apply_publish)
HT.register_precheck(TASK_PUBLISH, _publish_precheck)
HT.register_kind(TASK_SUB, label="Subscription approval", outcomes=("approve", "reject"), maker_checker=False,
                 on_complete=_apply_subscription, system_open=True)
HT.register_precheck(TASK_SUB, _sub_precheck)
HT.register_kind(TASK_AVAILABLE, label="New version available", outcomes=("done",), maker_checker=False,
                 system_open=True)
HT.register_kind(TASK_MOVE, label="Move account off a provided model", outcomes=("done",), maker_checker=False,
                 system_open=True)
HT.register_precheck(M.TASK_APPROVAL, _adoption_precheck)
HT.register_precheck(M.TASK_REVIEW, _adoption_precheck)
