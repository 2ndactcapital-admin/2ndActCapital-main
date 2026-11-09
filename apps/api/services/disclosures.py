"""DISCLOSURES1 — ONE disclosure library for the whole platform. Every new disclosure goes through it.

A disclosure is a KEY (``long_short.risks``, ``hypothetical.performance``, …) in ``public.disclosures``. Each key has
VERSIONS (``public.disclosure_versions``). A version is a FILE stored in Cloudflare R2 (PDF, DOCX, Markdown or
HTML) plus its plain-text SUMMARY, which is what a screen shows inline. It also carries:

  * its content hash (SHA-256 of the file's bytes);
  * its effective date;
  * its status (draft → published → retired; a draft may be withdrawn, which also marks it retired);
  * who drafted it and who published it. A different person must publish (maker-checker, also a CHECK).

SCOPES. A platform default can be overridden by an org, then a household, then an account:

    platform default  →  org  →  household  →  account

THE RESOLVER (:func:`resolve_many`, one query) picks, per key, the most specific PUBLISHED version in force on the
as-of date: ``effective_from <= as_of`` and (``effective_to`` is NULL or ``as_of < effective_to``). An account's
household is read from the account in the same statement. On a tie at one scope, the latest effective date wins,
then the highest version number. A draft never shows. A retired version stops on its retire date.

FALLBACK. Every key that replaced text in code keeps that EXACT text as its offline fallback (:data:`FALLBACK_SOURCES`
reads the original constants, never a copy). With no version in force, or with the library unreadable, the caller
still gets the old text, so nothing goes blank. :func:`resolve_safe` never raises; a failure runs in a savepoint,
so the caller's transaction survives it.

R2 LAYOUT (:func:`object_key`). Never overwritten: a new version is a new object; the key embeds the version
number and the hash.

    disclosures/platform/<key>/v<N>-<sha256>.<ext>
    disclosures/orgs/<org_id>/<key>/v<N>-<sha256>.<ext>
    disclosures/orgs/<org_id>/households/<household_id>/<key>/v<N>-<sha256>.<ext>
    disclosures/orgs/<org_id>/accounts/<account_id>/<key>/v<N>-<sha256>.<ext>

Uploads go through ``services.storage.upload_bytes`` and reads through ``services.storage.download_bytes``. Both
are called as module attributes, so a verify's fake transport at the same signatures replaces them. Reads are
PROXIED through the API under the caller's RLS context. Another org's version is a 404 before any storage call.

ACKNOWLEDGEMENTS (``public.disclosure_acknowledgements``). A row records:

  * who acknowledged which VERSION (its id + hash; a composite FK keeps the pair honest);
  * for what subject (a proposal, a plan, an account);
  * from which screen, and when.

:func:`require_current` is the gate a sensitive action calls. The version in force NOW must have been acknowledged
for that subject. A newer published version means acknowledging again.

WHO MAY DO WHAT (the routers gate; the service re-checks scope):

  * Super Admin manages platform defaults.
  * A holder of ``manage_disclosures`` manages the org's versions and its households' and accounts'.
  * Every action is audited (``audit_log``).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone

from services import storage
from services import sod  # S30 — the ONE maker-checker rule

log = logging.getLogger(__name__)

T_DISC = "public.disclosures"
T_VER = "public.disclosure_versions"
T_ACK = "public.disclosure_acknowledgements"
VIEW = "view_disclosures"
MANAGE = "manage_disclosures"
MAX_BYTES = 5 * 1024 * 1024
ROOT = "disclosures"
DEFAULT_LABEL = "default — have counsel review"

SCOPES = ("platform", "org", "household", "account")
SCOPE_RANK = {"platform": 1, "org": 2, "household": 3, "account": 4}
SCOPE_LABELS = {"platform": "Platform default", "org": "Org override", "household": "Household override",
                "account": "Account override"}
STATUS_LABELS = {"draft": "Draft — awaiting a second person to publish", "published": "Published",
                 "retired": "Retired"}
CATEGORY_LABELS = {"risk": "Risk statement", "hypothetical": "Hypothetical performance", "tax": "Tax",
                   "licence": "Licence notice", "product": "Product disclosure", "other": "Other"}
AUDIENCE_LABELS = {"staff": "Staff", "member": "Members", "both": "Staff and members"}
SOURCE_LABELS = {"library": "From the disclosure library", "fallback": "Built-in text (no library version in force)",
                 "none": "No version in force"}
SUBJECT_TYPES = ("long_short_proposal", "section351_plan", "account", "backtest", "proposal", "disclosure",
                 "model_subscription")
#: file extension → stored content type (the CHECK disclosure_versions_type_chk allows exactly these)
FILE_TYPES = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "md": "text/markdown",
    "html": "text/html",
    "htm": "text/html",
}
ITEM_RX = re.compile(r"^- \[([a-z0-9_]+)\] (.+)$")

#: The keys the platform defines (title, category, audience). Their catalog rows are created on first use.
KEYS: dict[str, dict] = {
    "long_short.risks": {"title": "Long/short (130/30, 150/50) — risks", "category": "risk", "audience": "both",
                         "description": "The risks a tax-aware long/short extension carries, including margin and "
                                        "short-sale risks. Acknowledged by the adviser before a proposal is "
                                        "submitted."},
    "hypothetical.performance": {"title": "Hypothetical and backtested performance", "category": "hypothetical",
                                 "audience": "both",
                                 "description": "Carried by every backtest screen, PDF and envelope. The firm's "
                                                "approved policy (SEC Marketing Rule 206(4)-1) still decides who "
                                                "may see one."},
    "tax.estimate_label": {"title": "Tax estimates", "category": "tax", "audience": "both",
                           "description": "What every estimated tax figure means."},
    # CASH01 — carried by every member-facing cash coverage figure (Treasury)
    "cash.coverage_estimate": {"title": "Cash coverage estimates", "category": "other", "audience": "both",
                               "description": "What a coverage figure (months, covered through, next gap) means."},
    # CASH02 — carried by every estimated sleeve yield (Treasury → cash policy)
    "cash.yield_estimate": {"title": "Cash sleeve yield estimates", "category": "other", "audience": "both",
                            "description": "What an estimated sleeve yield (a Treasury-curve proxy) means."},
    # CASH03 — carried by every after-tax figure of the ladder / instrument plan (Treasury)
    "cash.tax_estimate": {"title": "Cash after-tax estimates", "category": "tax", "audience": "both",
                          "description": "What an after-tax yield in the instrument plan means (the owner's tax "
                                         "profile and the instrument's exemptions)."},
    # CASH05 — registered for cash07 (the custodian sweep rate, ADV Item 5). CASH07 shows it on a MEMBER's Treasury
    # overview (routers/cash.py); the platform default ships as a DRAFT (a second Super Admin publishes it).
    "cash.sweep_rate": {"title": "Cash sweep rate", "category": "other", "audience": "both",
                        "description": "What the custodian's sweep rate on uninvested cash means, and that the rate "
                                       "and any spread are disclosed in the firm's Form ADV (Item 5)."},
    # CASH07 — carried by every payment instruction screen and the printable payment sheet
    "cash.payments": {"title": "Payments between your accounts", "category": "risk", "audience": "both",
                      "description": "What a payment instruction is and is not: ACH return risk, provisional "
                                     "settlement, value dates on business days, and that the custodian moves the money."},
    "prices.internal_use_notice": {"title": "Price data — internal use only", "category": "licence",
                                   "audience": "staff",
                                   "description": "The free end-of-day price source is licensed for internal use "
                                                  "and is never shown to members."},
    "replica.fund_affiliation": {"title": "Fund replica — no affiliation", "category": "product", "audience": "both",
                                 "description": "Carried by every model built from a fund's published holdings. "
                                                "{ticker} is replaced by the fund's ticker."},
    "direct_indexing.tax_overlay": {"title": "Direct indexing and the tax overlay", "category": "product",
                                    "audience": "both",
                                    "description": "Direct indexing / tax-loss harvesting: scope, risks and "
                                                   "limits."},
    "section351.exit": {"title": "Section 351 ETF conversion (exit)", "category": "product", "audience": "both",
                        "description": "Contributing a portfolio in kind to a new ETF under Section 351."},
    "options.overlay": {"title": "Options overlay", "category": "product", "audience": "both",
                        "description": "Writing or buying options on a portfolio."},
    "notes.replica": {"title": "Structured-note replica versus the note", "category": "product", "audience": "both",
                      "description": "A replica built from listed options compared with an issuer's structured "
                                     "note."},
    # rep15 — platform-published models. The ADV and the agreement are counsel's / the adviser's own documents:
    # no default text exists anywhere in code (operator items); only the member notice has a platform default.
    "model_provider.adv_2a": {"title": "Model provider — Form ADV Part 2A", "category": "product",
                              "audience": "both",
                              "description": "The registered adviser entity's ADV Part 2A brochure, published at the "
                                             "provider org's scope. Required before the publishing gate opens."},
    "model_provider.subadvisory_agreement": {"title": "Sub-advisory agreement (model provider)", "category": "other",
                                             "audience": "staff",
                                             "description": "The agreement between the model provider and a "
                                                            "subscribing org. A platform default, overridden by a "
                                                            "tenant-specific version; acknowledged by version before "
                                                            "a subscription is requested."},
    "model_provider.member_notice": {"title": "Model provided by a sub-adviser", "category": "product",
                                     "audience": "both",
                                     "description": "Shown on every proposal for a provided model. {provider} is "
                                                    "replaced by the sub-adviser's name."},
}


#: rep15 — keys the platform defines but ships NO default text for: the provider's ADV (the provider org's own
#: filing) and the sub-advisory agreement (counsel's text). The defaults loader skips them; a library version is the
#: only source — with none in force the resolver reports 'none', never a fallback.
NO_PLATFORM_DEFAULT = frozenset({"model_provider.adv_2a", "model_provider.subadvisory_agreement"})


class DisclosureError(ValueError):
    """A request the rules refuse (422)."""


class DisclosureNotFound(DisclosureError, LookupError):
    """Not found in the caller's reach (404)."""


class DisclosureConflict(DisclosureError):
    """The state does not allow it (409)."""


class DisclosureForbidden(PermissionError):
    """The caller may not act at this scope (403)."""


class MakerCheckerViolation(PermissionError):
    """The drafter may not publish their own version (403)."""


class StorageUnavailable(RuntimeError):
    """R2 could not be written or read (503) — nothing was recorded."""


class AcknowledgementRequired(DisclosureConflict):
    """The version in force has not been acknowledged for this subject (409)."""


# ═══ Fallbacks — the EXACT text the code carried before the library ═════════════════════════════════════════════
def _risk_lines(risks) -> str:
    return "\n".join(f"- [{k}] {t}" for k, t in risks)


def _fallback_long_short() -> str:
    from services import long_short_rules as R

    return _risk_lines(R.RISKS)


def _fallback_hypothetical() -> str:
    from services import hypothetical_policy as HP

    return HP.NO_POLICY


def _fallback_tax() -> str:
    from services import tax_profiles as TP

    return TP.ESTIMATE


def _fallback_prices() -> str:
    from services import security_master_jobs as J

    return J.PRICE_GAP_NOTE


def _fallback_replica() -> str:
    from services import replica_terms as T

    return T.FUND_DISCLOSURE_TEMPLATE


#: key → a function returning the exact pre-library text (read from the ORIGINAL constant, never copied here)
def _fallback_coverage() -> str:
    from services import cash_coverage as CC

    return CC.COVERAGE_ESTIMATE


def _fallback_yield() -> str:
    from services import cash_policy as CP

    return CP.YIELD_ESTIMATE


def _fallback_tax_estimate() -> str:
    from services import cash_instruments as CI

    return CI.TAX_ESTIMATE


def _fallback_sweep_rate() -> str:
    from services import cash_holdings as CH

    return CH.SWEEP_RATE_ESTIMATE


def _fallback_payments() -> str:
    from services import payments as PS

    return PS.PAYMENTS_DISCLOSURE


def _fallback_member_notice() -> str:
    from services import model_provider as PM

    return PM.MEMBER_NOTICE_FALLBACK


FALLBACK_SOURCES = {
    "model_provider.member_notice": _fallback_member_notice,
    "long_short.risks": _fallback_long_short,
    "hypothetical.performance": _fallback_hypothetical,
    "tax.estimate_label": _fallback_tax,
    "cash.coverage_estimate": _fallback_coverage,
    "cash.yield_estimate": _fallback_yield,
    "cash.tax_estimate": _fallback_tax_estimate,
    "cash.sweep_rate": _fallback_sweep_rate,
    "cash.payments": _fallback_payments,
    "prices.internal_use_notice": _fallback_prices,
    "replica.fund_affiliation": _fallback_replica,
}


def fallback_text(key: str) -> str | None:
    fn = FALLBACK_SOURCES.get(key)
    return fn() if fn else None


def items_of(text: str | None) -> list[dict]:
    """A list-shaped summary ("- [key] text" lines) → [{key, text}] — the long/short risks' shape."""
    out = []
    for line in (text or "").splitlines():
        m = ITEM_RX.match(line.strip())
        if m:
            out.append({"key": m.group(1), "text": m.group(2).strip()})
    return out


# ═══ Small helpers ══════════════════════════════════════════════════════════════════════════════════════════════
def _uid(v, what: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(v))
    except (TypeError, ValueError, AttributeError):
        raise DisclosureNotFound(f"{what} not found") from None


def _iso(v):
    return v.isoformat() if isinstance(v, (date, datetime)) else v


def _s(v):
    return str(v) if v is not None else None


def check_key(key: str) -> str:
    k = (key or "").strip()
    if not re.fullmatch(r"[a-z0-9_]+(\.[a-z0-9_]+)+", k) or len(k) > 120:
        raise DisclosureNotFound("disclosure key not found")
    return k


def file_kind(file_name: str) -> tuple[str, str]:
    """(extension, content type) from the file name — PDF, DOCX, Markdown or HTML only."""
    ext = (file_name or "").rsplit(".", 1)[-1].lower() if "." in (file_name or "") else ""
    if ext not in FILE_TYPES:
        raise DisclosureError("a disclosure file is PDF, DOCX, Markdown (.md) or HTML")
    return ("html" if ext == "htm" else ext), FILE_TYPES[ext]


def object_key(*, key: str, scope: str, org_id=None, household_id=None, account_id=None, version_no: int,
               sha256: str, ext: str) -> str:
    """The R2 object key — under the org's folder (or the platform's), never reused (version + hash)."""
    if scope == "platform":
        base = f"{ROOT}/platform"
    elif scope == "org":
        base = f"{ROOT}/orgs/{org_id}"
    elif scope == "household":
        base = f"{ROOT}/orgs/{org_id}/households/{household_id}"
    elif scope == "account":
        base = f"{ROOT}/orgs/{org_id}/accounts/{account_id}"
    else:
        raise DisclosureError(f"scope is one of {list(SCOPES)}")
    return f"{base}/{key}/v{int(version_no)}-{sha256}.{ext}"


def why(scope: str | None, source: str) -> str:
    if source != "library":
        return SOURCE_LABELS[source]
    return SCOPE_LABELS.get(scope or "", scope or "")


async def _audit(conn, org_id, actor_id, action: str, resource_id, payload: dict) -> None:
    await conn.execute(
        "INSERT INTO public.audit_log (org_id, user_id, action, resource_type, resource_id, payload) "
        "VALUES ($1::uuid, $2::uuid, $3, 'disclosure_version', $4::uuid, $5::jsonb)",
        str(org_id), str(actor_id), action, str(resource_id), json.dumps(payload, default=str, sort_keys=True))


async def _super_admin_ctx(conn) -> bool:
    return (await conn.fetchval("SELECT NULLIF(current_setting('app.is_super_admin', true), '')")) == "true"


# ═══ The catalog ════════════════════════════════════════════════════════════════════════════════════════════════
async def catalog_row(conn, key: str) -> dict | None:
    r = await conn.fetchrow(f"SELECT * FROM {T_DISC} WHERE key = $1 AND system_to IS NULL", key)
    return dict(r) if r else None


async def ensure_catalog(conn, key: str, actor_id=None) -> dict:
    """The catalog row of a KNOWN key (created on first use, from :data:`KEYS`). The catalog is platform data, so the
    insert runs with the super-admin GUC set LOCAL around this one statement and restored right after."""
    row = await catalog_row(conn, key)
    if row:
        return row
    spec = KEYS.get(key)
    if spec is None:
        raise DisclosureNotFound(f"'{key}' is not a disclosure key the platform defines")
    prev = await conn.fetchval("SELECT current_setting('app.is_super_admin', true)")
    await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
    try:
        await conn.execute(
            f"INSERT INTO {T_DISC} (key, title, category, audience, description, created_by) VALUES ($1, $2, $3, $4, "
            "$5, $6::uuid) ON CONFLICT (key) WHERE system_to IS NULL DO NOTHING", key, spec["title"],
            spec["category"], spec["audience"], spec.get("description"), _s(actor_id))
    finally:
        await conn.execute("SELECT set_config('app.is_super_admin', $1, true)", prev or "")
    return await catalog_row(conn, key)


# ═══ The resolver — one function, one query ═════════════════════════════════════════════════════════════════════
RESOLVE_SQL = f"""
WITH acct AS (
  SELECT a.id, a.household_id FROM public.accounts a
   WHERE a.id = $3::uuid AND a.org_id = $2::uuid AND a.system_to IS NULL AND a.valid_to IS NULL
), target AS (
  SELECT $2::uuid AS org_id, COALESCE($4::uuid, (SELECT household_id FROM acct)) AS household_id,
         (SELECT id FROM acct) AS account_id
)
SELECT DISTINCT ON (d.key)
       d.key, d.title, d.category, d.audience, d.id AS disclosure_id,
       v.id AS version_id, v.scope, v.org_id, v.household_id, v.account_id, v.version_no, v.summary,
       v.content_sha256, v.file_name, v.content_type, v.size_bytes, v.effective_from, v.effective_to, v.status,
       v.published_at
  FROM {T_DISC} d
  JOIN {T_VER} v ON v.disclosure_id = d.id AND v.system_to IS NULL
  CROSS JOIN target t
 WHERE d.system_to IS NULL AND d.key = ANY($1::text[])
   AND v.status IN ('published', 'retired') AND v.published_by IS NOT NULL
   AND v.effective_from <= $5::date AND (v.effective_to IS NULL OR $5::date < v.effective_to)
   AND (   v.scope = 'platform'
        OR (v.scope = 'org' AND v.org_id = t.org_id)
        OR (v.scope = 'household' AND v.org_id = t.org_id AND v.household_id = t.household_id)
        OR (v.scope = 'account' AND v.org_id = t.org_id AND v.account_id = t.account_id))
 ORDER BY d.key,
          CASE v.scope WHEN 'account' THEN 4 WHEN 'household' THEN 3 WHEN 'org' THEN 2 ELSE 1 END DESC,
          v.effective_from DESC, v.version_no DESC
"""


def _resolved(key: str, r: dict | None) -> dict:
    spec = KEYS.get(key, {})
    if r is None:
        fb = fallback_text(key)
        source = "fallback" if fb is not None else "none"
        return {"key": key, "title": spec.get("title", key), "category": spec.get("category"),
                "audience": spec.get("audience"), "source": source, "why": why(None, source), "scope": None,
                "version_id": None, "version_no": None, "content_sha256": None, "text": fb, "items": items_of(fb),
                "effective_from": None, "effective_to": None, "file_href": None}
    return {"key": key, "title": r["title"], "category": r["category"], "audience": r["audience"],
            "source": "library", "why": why(r["scope"], "library"), "scope": r["scope"],
            "version_id": str(r["version_id"]), "version_no": r["version_no"], "content_sha256": r["content_sha256"],
            "text": r["summary"], "items": items_of(r["summary"]), "file_name": r["file_name"],
            "content_type": r["content_type"], "effective_from": _iso(r["effective_from"]),
            "effective_to": _iso(r["effective_to"]), "disclosure_id": str(r["disclosure_id"]),
            "household_id": _s(r["household_id"]), "account_id": _s(r["account_id"]),
            "file_href": f"/api/disclosures/versions/{r['version_id']}/file"}


async def resolve_many(conn, keys, *, org_id, household_id=None, account_id=None, as_of: date | None = None) -> dict:
    """{key: resolved} — the most specific published version in force on ``as_of`` (today when None), else the
    fallback. Raises on a database error (see :func:`resolve_safe`)."""
    ks = sorted({check_key(k) for k in keys})
    on = as_of or date.today()
    rows = await conn.fetch(RESOLVE_SQL, ks, str(org_id), _s(account_id), _s(household_id), on)
    by_key = {r["key"]: dict(r) for r in rows}
    return {k: _resolved(k, by_key.get(k)) for k in ks}


async def resolve(conn, key: str, **kw) -> dict:
    return (await resolve_many(conn, [key], **kw))[check_key(key)]


async def resolve_safe(conn, key: str, **kw) -> dict:
    """:func:`resolve`, but it NEVER raises: a failure (the table missing, a lost connection) runs inside a savepoint
    and yields the fallback, so the screen still shows the old text and the caller's transaction is intact."""
    try:
        async with conn.transaction():
            return await resolve(conn, key, **kw)
    except Exception as exc:  # noqa: BLE001
        log.warning("disclosure %s: resolver failed (%s: %s) — using the built-in text", key, type(exc).__name__, exc)
        out = _resolved(check_key(key), None)
        out["degraded"] = True
        return out


PUBLIC_FIELDS = ("key", "title", "category", "audience", "source", "why", "scope", "version_id", "version_no",
                 "content_sha256", "text", "items", "effective_from", "effective_to", "file_href")


def public(res: dict) -> dict:
    """What an envelope carries of a resolved disclosure (no storage key, no internal ids)."""
    return {k: res.get(k) for k in PUBLIC_FIELDS}


async def text_for(conn, key: str, **kw) -> str | None:
    return (await resolve_safe(conn, key, **kw))["text"]


# ═══ Versions ═══════════════════════════════════════════════════════════════════════════════════════════════════
VERSION_COLS = ("disclosure_id", "scope", "org_id", "household_id", "account_id", "version_no", "r2_key", "r2_bucket",
                "file_name", "content_type", "size_bytes", "content_sha256", "summary", "effective_from",
                "effective_to", "status", "drafted_by", "drafted_at", "published_by", "published_at", "retired_by",
                "retired_at", "retire_reason", "note", "created_at")


def version_out(r: dict, names: dict | None = None) -> dict:
    names = names or {}
    return {"id": str(r["id"]), "disclosure_id": str(r["disclosure_id"]), "scope": r["scope"],
            "scope_label": SCOPE_LABELS[r["scope"]], "org_id": _s(r["org_id"]), "household_id": _s(r["household_id"]),
            "account_id": _s(r["account_id"]), "version_no": r["version_no"], "r2_key": r["r2_key"],
            "file_name": r["file_name"], "content_type": r["content_type"], "size_bytes": r["size_bytes"],
            "content_sha256": r["content_sha256"], "summary": r["summary"], "effective_from": _iso(r["effective_from"]),
            "effective_to": _iso(r["effective_to"]), "status": r["status"], "status_label": STATUS_LABELS[r["status"]],
            "drafted_by": _s(r["drafted_by"]), "drafted_by_name": names.get(_s(r["drafted_by"])),
            "drafted_at": _iso(r["drafted_at"]), "published_by": _s(r["published_by"]),
            "published_by_name": names.get(_s(r["published_by"])), "published_at": _iso(r["published_at"]),
            "retired_by": _s(r["retired_by"]), "retired_at": _iso(r["retired_at"]),
            "retire_reason": r["retire_reason"], "note": r["note"],
            "file_href": f"/api/disclosures/versions/{r['id']}/file"}


async def user_names(conn, ids) -> dict:
    ids = sorted({str(i) for i in ids if i})
    if not ids:
        return {}
    rows = await conn.fetch("SELECT id, COALESCE(NULLIF(full_name, ''), email) AS n FROM public.users "
                            "WHERE id = ANY($1::uuid[])", ids)
    return {str(r["id"]): r["n"] for r in rows}


async def load_version(conn, version_id, *, lock: bool = False) -> dict:
    r = await conn.fetchrow(f"SELECT v.*, d.key, d.audience, d.title FROM {T_VER} v JOIN {T_DISC} d ON d.id = "
                            f"v.disclosure_id WHERE v.id = $1::uuid AND v.system_to IS NULL"
                            f"{' FOR UPDATE OF v' if lock else ''}", _uid(version_id, "disclosure version"))
    if r is None:
        raise DisclosureNotFound("disclosure version not found")
    return dict(r)


async def _target(conn, org_id, scope: str, household_id, account_id) -> dict:
    """The (org, household, account) a new version is for — the household / account must be the caller's org's."""
    if scope not in SCOPES:
        raise DisclosureError(f"scope is one of {list(SCOPES)}")
    if scope == "platform":
        if household_id or account_id:
            raise DisclosureError("a platform default names no household or account")
        return {"org_id": None, "household_id": None, "account_id": None}
    if scope == "org":
        if household_id or account_id:
            raise DisclosureError("an org override names no household or account")
        return {"org_id": str(org_id), "household_id": None, "account_id": None}
    if scope == "household":
        if not household_id or account_id:
            raise DisclosureError("a household override names the household (and no account)")
        hid = _uid(household_id, "household")
        ok = await conn.fetchval("SELECT 1 FROM public.households WHERE id = $1 AND org_id = $2::uuid", hid,
                                 str(org_id))
        if not ok:
            raise DisclosureNotFound("household not found")
        return {"org_id": str(org_id), "household_id": str(hid), "account_id": None}
    if not account_id or household_id:
        raise DisclosureError("an account override names the account (and no household)")
    aid = _uid(account_id, "account")
    ok = await conn.fetchval("SELECT 1 FROM public.accounts WHERE id = $1 AND org_id = $2::uuid AND system_to IS NULL "
                             "AND valid_to IS NULL", aid, str(org_id))
    if not ok:
        raise DisclosureNotFound("account not found")
    return {"org_id": str(org_id), "household_id": None, "account_id": str(aid)}


def _may_manage(scope: str, *, is_super_admin: bool) -> None:
    if scope == "platform" and not is_super_admin:
        raise DisclosureForbidden("only a Super Admin manages platform defaults")


async def upload_version(pool, *, org_id, actor_id, is_super_admin: bool, key: str, scope: str, file_name: str,
                         data: bytes, summary: str, effective_from: date | None = None, household_id=None,
                         account_id=None, note: str | None = None) -> dict:
    """A new DRAFT version: the file goes to R2 under its folder (a new object — never an overwrite), the row
    records its key, hash, size and summary. A second person publishes it (:func:`publish`)."""
    from starlette.concurrency import run_in_threadpool

    key = check_key(key)
    _may_manage(scope, is_super_admin=is_super_admin)
    ext, ctype = file_kind(file_name)
    if not data:
        raise DisclosureError("the file is empty")
    if len(data) > MAX_BYTES:
        raise DisclosureError(f"a disclosure file is at most {MAX_BYTES // (1024 * 1024)} MB")
    text = (summary or "").strip()
    if not text:
        raise DisclosureError("a version needs its plain-text summary (what screens show inline)")
    if len(text) > 6000:
        raise DisclosureError("the summary is at most 6,000 characters — the full text is the file")
    sha = hashlib.sha256(data).hexdigest()
    eff = effective_from or date.today()
    async with pool.acquire() as conn:
        async with conn.transaction():
            cat = await ensure_catalog(conn, key, actor_id)
            tgt = await _target(conn, org_id, scope, household_id, account_id)
            n = int(await conn.fetchval(
                f"SELECT COALESCE(max(version_no), 0) + 1 FROM {T_VER} WHERE disclosure_id = $1 AND scope = $2 AND "
                "org_id IS NOT DISTINCT FROM $3::uuid AND household_id IS NOT DISTINCT FROM $4::uuid AND "
                "account_id IS NOT DISTINCT FROM $5::uuid AND system_to IS NULL", cat["id"], scope, tgt["org_id"],
                tgt["household_id"], tgt["account_id"]))
            r2_key = object_key(key=key, scope=scope, org_id=tgt["org_id"], household_id=tgt["household_id"],
                                account_id=tgt["account_id"], version_no=n, sha256=sha, ext=ext)
            if await conn.fetchval(f"SELECT 1 FROM {T_VER} WHERE r2_key = $1", r2_key):
                raise DisclosureConflict("that object already exists — a stored file is never overwritten")
            row = await conn.fetchrow(
                f"""INSERT INTO {T_VER} (disclosure_id, scope, org_id, household_id, account_id, version_no, r2_key,
                        r2_bucket, file_name, content_type, size_bytes, content_sha256, summary, effective_from,
                        status, drafted_by, note)
                    VALUES ($1, $2, $3::uuid, $4::uuid, $5::uuid, $6, $7, $8, $9, $10, $11, $12, $13, $14, 'draft',
                            $15::uuid, $16) RETURNING *""",
                cat["id"], scope, tgt["org_id"], tgt["household_id"], tgt["account_id"], n, r2_key,
                storage.DEFAULT_BUCKET, (file_name or "").strip()[:255], ctype, len(data), sha, text, eff,
                str(actor_id), (note or "").strip() or None)
            await _audit(conn, org_id, actor_id, "disclosure.version_drafted", row["id"],
                         {"key": key, "scope": scope, "version_no": n, "r2_key": r2_key, "sha256": sha,
                          "household_id": tgt["household_id"], "account_id": tgt["account_id"]})
            try:
                await run_in_threadpool(storage.upload_bytes, r2_key, data, ctype, storage.DEFAULT_BUCKET)
            except Exception as exc:  # noqa: BLE001 — nothing is recorded when the file is not stored
                raise StorageUnavailable(f"the file could not be stored ({type(exc).__name__}) — nothing was "
                                         "recorded") from exc
    return {**dict(row), "key": key}


async def _archive(conn, row: dict, ts) -> None:
    cols = ", ".join(VERSION_COLS)
    await conn.execute(
        f"INSERT INTO {T_VER} ({cols}, archived_from_id, system_from, system_to) "
        f"SELECT {cols}, id, system_from, $2 FROM {T_VER} WHERE id = $1 AND system_to IS NULL", row["id"], ts)


async def publish(pool, *, org_id, actor_id, is_super_admin: bool, version_id) -> dict:
    """Draft → published, by a DIFFERENT person than the drafter (maker-checker; also a CHECK)."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await load_version(conn, version_id, lock=True)
            _may_manage(row["scope"], is_super_admin=is_super_admin)
            if row["scope"] != "platform" and str(row["org_id"]) != str(org_id):
                raise DisclosureNotFound("disclosure version not found")
            if row["status"] != "draft":
                raise DisclosureConflict(f"the version is {row['status']}, not a draft")
            await sod.enforce(conn, org_id=row["org_id"] or org_id, item_type=sod.DISCLOSURE_VERSION, checker_id=actor_id,
                           maker_ids=[row["drafted_by"]], item_id=row["id"], site="disclosures.publish",
                           record=sod.RECORD_ALL, refuse=lambda d: MakerCheckerViolation("maker-checker: you uploaded this version, so a different person must "
                                                                "publish it"))
            ts = await conn.fetchval("SELECT clock_timestamp()")
            await _archive(conn, row, ts)
            new = await conn.fetchrow(
                f"UPDATE {T_VER} SET status = 'published', published_by = $2::uuid, published_at = $3, "
                "system_from = $3 WHERE id = $1 AND system_to IS NULL RETURNING *", row["id"], str(actor_id), ts)
            await _audit(conn, org_id, actor_id, "disclosure.version_published", row["id"],
                         {"key": row["key"], "scope": row["scope"], "version_no": row["version_no"],
                          "drafted_by": str(row["drafted_by"]), "sha256": row["content_sha256"]})
    return {**dict(new), "key": row["key"]}


async def retire(pool, *, org_id, actor_id, is_super_admin: bool, version_id, reason: str,
                 retire_on: date | None = None) -> dict:
    """A published version stops being in force ON ``retire_on`` (today by default); a draft is withdrawn."""
    why_ = (reason or "").strip()
    if not why_:
        raise DisclosureError("retiring a version needs a reason")
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await load_version(conn, version_id, lock=True)
            _may_manage(row["scope"], is_super_admin=is_super_admin)
            if row["scope"] != "platform" and str(row["org_id"]) != str(org_id):
                raise DisclosureNotFound("disclosure version not found")
            if row["status"] == "retired":
                raise DisclosureConflict("the version is already retired")
            on = retire_on or date.today()
            end = None if row["status"] == "draft" else max(on, row["effective_from"])
            ts = await conn.fetchval("SELECT clock_timestamp()")
            await _archive(conn, row, ts)
            new = await conn.fetchrow(
                f"UPDATE {T_VER} SET status = 'retired', retired_by = $2::uuid, retired_at = $3, retire_reason = $4, "
                "effective_to = $5, system_from = $3 WHERE id = $1 AND system_to IS NULL RETURNING *", row["id"],
                str(actor_id), ts, why_, end)
            await _audit(conn, org_id, actor_id, "disclosure.version_retired", row["id"],
                         {"key": row["key"], "scope": row["scope"], "version_no": row["version_no"],
                          "effective_to": _iso(end), "reason": why_, "was": row["status"]})
    return {**dict(new), "key": row["key"]}


async def read_file(conn, row: dict) -> bytes:
    """The stored bytes, proxied — and refused if they no longer match the recorded hash."""
    from starlette.concurrency import run_in_threadpool

    try:
        data = await run_in_threadpool(storage.download_bytes, row["r2_key"], row.get("r2_bucket"))
    except Exception as exc:  # noqa: BLE001
        raise StorageUnavailable(f"the file could not be read ({type(exc).__name__})") from exc
    if hashlib.sha256(data).hexdigest() != row["content_sha256"]:
        raise DisclosureConflict("the stored file does not match its recorded hash — it is not shown")
    return data


async def versions_of(conn, disclosure_id, *, org_id, household_id=None, account_id=None,
                      scopes=("platform", "org", "household", "account")) -> list[dict]:
    """The live versions of a key the caller's org can see: platform + its own (RLS also enforces the org)."""
    rows = await conn.fetch(
        f"SELECT * FROM {T_VER} WHERE disclosure_id = $1 AND system_to IS NULL AND scope = ANY($2::text[]) AND "
        "(scope = 'platform' OR org_id = $3::uuid) AND ($4::uuid IS NULL OR household_id = $4::uuid OR scope <> "
        "'household') AND ($5::uuid IS NULL OR account_id = $5::uuid OR scope <> 'account') "
        "ORDER BY CASE scope WHEN 'account' THEN 4 WHEN 'household' THEN 3 WHEN 'org' THEN 2 ELSE 1 END DESC, "
        "version_no DESC", disclosure_id, list(scopes), str(org_id), _s(household_id), _s(account_id))
    return [dict(r) for r in rows]


# ═══ Members: what applies to them ══════════════════════════════════════════════════════════════════════════════
async def member_targets(pool, conn, org_id, user_id) -> dict:
    """The households and accounts a member can see (backtest.member_view's path: visible entities → households via
    memberships / accounts; accounts by primary entity, household or account_owners)."""
    from services.document_embedding import visible_entity_ids

    ents = [str(x) for x in await visible_entity_ids(pool, org_id, user_id, False)]
    if not ents:
        return {"households": [], "accounts": []}
    hhs = {str(r["household_id"]) for r in await conn.fetch(
        "SELECT household_id FROM public.household_memberships WHERE entity_id = ANY($1::uuid[]) UNION "
        "SELECT household_id FROM public.accounts WHERE org_id = $2::uuid AND household_id IS NOT NULL "
        "AND primary_entity_id = ANY($1::uuid[]) AND system_to IS NULL AND valid_to IS NULL", ents, str(org_id))
        if r["household_id"]}
    accts = {str(r["id"]) for r in await conn.fetch(
        "SELECT id FROM public.accounts WHERE org_id = $1::uuid AND system_to IS NULL AND valid_to IS NULL AND "
        "(primary_entity_id = ANY($2::uuid[]) OR household_id = ANY($3::uuid[]) OR id IN (SELECT account_id FROM "
        "public.account_owners WHERE entity_id = ANY($2::uuid[])))", str(org_id), ents, sorted(hhs))}
    return {"households": sorted(hhs), "accounts": sorted(accts)}


async def member_disclosures(pool, conn, org_id, user_id, as_of: date | None = None) -> list[dict]:
    """Every PUBLISHED, member-audience version in force that applies to the member: the org's (or the platform's)
    and the most specific one for each of the member's households and accounts. A staff-only key never appears."""
    keys = [k for k, s in KEYS.items() if s["audience"] in ("member", "both")]
    keys += [r["key"] for r in await conn.fetch(f"SELECT key FROM {T_DISC} WHERE system_to IS NULL AND audience IN "
                                                "('member', 'both')") if r["key"] not in keys]
    t = await member_targets(pool, conn, org_id, user_id)
    seen: dict[str, dict] = {}
    targets = [{}] + [{"household_id": h} for h in t["households"]] + [{"account_id": a} for a in t["accounts"]]
    for tg in targets:
        for k, res in (await resolve_many(conn, keys, org_id=org_id, as_of=as_of, **tg)).items():
            if res["source"] == "library" and res["audience"] in ("member", "both"):
                seen.setdefault(res["version_id"], {**res, "applies_to": tg or {"org": True}})
    return sorted(seen.values(), key=lambda r: (r["key"], SCOPE_RANK.get(r["scope"], 0)))


# ═══ Acknowledgements ═══════════════════════════════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class Subject:
    subject_type: str
    subject_id: str | None = None
    account_id: str | None = None
    household_id: str | None = None


async def acknowledge(conn, *, org_id, user_id, resolved: dict, subject: Subject, screen: str) -> dict:
    """Record that ``user_id`` acknowledged the RESOLVED version (id + hash) for ``subject``. Idempotent per
    (user, version, subject). A fallback (no library version) has nothing to acknowledge — returns None."""
    if subject.subject_type not in SUBJECT_TYPES:
        raise DisclosureError(f"subject_type is one of {list(SUBJECT_TYPES)}")
    if resolved.get("source") != "library" or not resolved.get("version_id"):
        return None
    have = await conn.fetchrow(
        f"SELECT * FROM {T_ACK} WHERE org_id = $1::uuid AND version_id = $2::uuid AND user_id = $3::uuid AND "
        "subject_type = $4 AND subject_id IS NOT DISTINCT FROM $5::uuid", str(org_id), resolved["version_id"],
        str(user_id), subject.subject_type, subject.subject_id)
    if have:
        return dict(have)
    row = await conn.fetchrow(
        f"INSERT INTO {T_ACK} (org_id, disclosure_id, version_id, content_sha256, user_id, subject_type, subject_id, "
        "account_id, screen) VALUES ($1::uuid, $2::uuid, $3::uuid, $4, $5::uuid, $6, $7::uuid, $8::uuid, $9) "
        "RETURNING *", str(org_id), resolved["disclosure_id"], resolved["version_id"], resolved["content_sha256"],
        str(user_id), subject.subject_type, subject.subject_id, subject.account_id, (screen or "").strip()[:200])
    await conn.execute(
        "INSERT INTO public.audit_log (org_id, user_id, action, resource_type, resource_id, payload) VALUES "
        "($1::uuid, $2::uuid, 'disclosure.acknowledged', 'disclosure_version', $3::uuid, $4::jsonb)", str(org_id),
        str(user_id), resolved["version_id"], json.dumps({"key": resolved["key"], "subject_type": subject.subject_type,
                                                          "subject_id": subject.subject_id, "screen": screen,
                                                          "sha256": resolved["content_sha256"]}, sort_keys=True))
    return dict(row)


async def latest_ack(conn, *, org_id, user_id, disclosure_id, subject: Subject) -> dict | None:
    r = await conn.fetchrow(
        f"SELECT * FROM {T_ACK} WHERE org_id = $1::uuid AND user_id = $2::uuid AND disclosure_id = $3::uuid AND "
        "subject_type = $4 AND subject_id IS NOT DISTINCT FROM $5::uuid ORDER BY acknowledged_at DESC LIMIT 1",
        str(org_id), str(user_id), str(disclosure_id), subject.subject_type, subject.subject_id)
    return dict(r) if r else None


async def ack_state(conn, *, org_id, user_id, key: str, subject: Subject, as_of: date | None = None) -> dict:
    """The version in force now for the subject, and whether ``user_id`` acknowledged EXACTLY it."""
    res = await resolve(conn, key, org_id=org_id, household_id=subject.household_id, account_id=subject.account_id,
                        as_of=as_of)
    if res["source"] != "library":
        return {"resolved": res, "required": False, "acknowledged": None, "current": True}
    last = await latest_ack(conn, org_id=org_id, user_id=user_id, disclosure_id=res["disclosure_id"],
                            subject=subject) if user_id else None
    current = bool(last and str(last["version_id"]) == res["version_id"]
                   and last["content_sha256"] == res["content_sha256"])
    return {"resolved": res, "required": True, "acknowledged": {"version_id": str(last["version_id"]),
                                                               "content_sha256": last["content_sha256"],
                                                               "acknowledged_at": _iso(last["acknowledged_at"])}
            if last else None, "current": current}


async def require_current(conn, *, org_id, user_id, key: str, subject: Subject, as_of: date | None = None) -> dict:
    """The gate: refuse (AcknowledgementRequired) unless the version in force NOW was acknowledged by ``user_id`` for
    ``subject``. With no library version in force there is nothing to acknowledge here (the caller's own rule, if
    any, still applies)."""
    st = await ack_state(conn, org_id=org_id, user_id=user_id, key=key, subject=subject, as_of=as_of)
    if st["required"] and not st["current"]:
        res = st["resolved"]
        if st["acknowledged"]:
            raise AcknowledgementRequired(
                f"a newer version of '{res['title']}' (version {res['version_no']}) was published since your "
                "acknowledgement — read it and acknowledge again")
        raise AcknowledgementRequired(f"acknowledge '{res['title']}' (version {res['version_no']}) first")
    return st


def vocabularies(*, can_write: bool, can_manage_platform: bool) -> dict:
    def pairs(d):
        return [{"key": k, "label": v} for k, v in d.items()]

    editable = (["upload", "publish", "retire"] if can_write else [])
    return {"scopes": pairs(SCOPE_LABELS), "statuses": pairs(STATUS_LABELS), "categories": pairs(CATEGORY_LABELS),
            "audiences": pairs(AUDIENCE_LABELS), "sources": pairs(SOURCE_LABELS),
            "file_types": sorted({k for k in FILE_TYPES if k != "htm"}), "max_bytes": MAX_BYTES,
            "default_label": DEFAULT_LABEL, "editable": editable, "inline_editable": [],
            "manageable_scopes": ([s for s in SCOPES if s != "platform"] if can_write else [])
            + (["platform"] if can_manage_platform else [])}


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
