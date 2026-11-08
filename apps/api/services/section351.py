"""REP14 — the Section 351 exit: candidates, sponsor profiles, the per-account 25 % / 50 % test, the fix-up proposal,
the pool and its control test, the basis record, consents + the project tracker, and the in-kind transfer
instructions. PLANNING SIDE, MANUAL-FIRST: nothing here trades, sells or moves a security by itself. The rules
(with their citations) are services/section351_rules.py; this module reads and writes.

ROLES (Joe, 2026-11-07). A third-party ETF SPONSOR runs the new fund (2nd Act is not an ETF issuer); a plan names its
sponsor from a SPONSOR PROFILE (org admin edits; every edit is a new version; a plan records the version it used).
2nd Act is the members' ADVISER: it identifies candidates, runs the tests, collects consents and basis records and
instructs the custodian; the sponsor runs the fund and its own final compliance review.

ELIGIBILITY (sponsor-profile defaults, editable): U.S. persons only; individual, joint, trust and S-corp accounts
eligible; partnerships / LLCs "needs sponsor review"; C-corps and non-U.S. persons not eligible (a retirement or
tax-exempt account is refused too — there is no gain to defer; our reading, for counsel). Ineligible assets
(excluded from the contribution and listed): restricted or illiquid securities, derivatives (option legs; short
positions are never lots, so never contributed), crypto, physical commodities, commodity pools, grantor trusts,
partnership interests. Cash per the sponsor's rule (default: not contributed — stays in the account).

ACCOUNT ELIGIBILITY (Task 2): a replica account (an active model link) with LOT-LEVEL data for every holding
(average cost is not enough — Joe's spec), not held-away, authority 'managed' (rep7b's word; the prompt's 'manage').

"READY" GATES (every one, every account): eligible owner (a partnership only after the sponsor's recorded review);
U.S. person confirmed by the adviser (source + date) when the sponsor requires it; the 25 / 50 test PASSES at the
plan's valuation date (re-run at ready); the member's written consent (document, date, signer); the member's tax
adviser's confirmation (recorded — never inferred); the in-force section351.exit disclosure version acknowledged
for that account (a newly published version needs re-acknowledging; no published version = not ready — the
disclosure is never text in code). Plan: at least one account and the control test passes.

APPROVAL: ready → submitted (a human task, approve_proposals) → a DIFFERENT person approves (maker-checker: task,
service and CHECK) → one "in-kind transfer out" instruction per contributed lot (public.section351_transfers).
Routing: an adapter that does not DECLARE in_kind_transfers refuses at submit; the manual route (the printed
instruction sheet) is always offered. After the custodian's confirmation (a person records it): the lots close BY
TRANSFER (realized_gain_loss stays NULL — nothing is realised, so the loss bank and realized YTD never count it) and
the ETF position is recorded with the carryover basis and tacked acquisition dates.
"""

from __future__ import annotations

import csv
import hashlib
import io
import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, localcontext

from services import human_tasks as HT
from services import rep10_common as C
from services import section351_rules as R

log = logging.getLogger(__name__)

VIEW, MANAGE, APPROVE = "view_proposals", "manage_proposals", "approve_proposals"
ADMIN = "manage_org_settings"
T_PROFILE = "public.section351_sponsor_profiles"
T_SET = "public.section351_settings"
T_CLASS = "public.section351_asset_classes"
T_PLAN = "public.section351_plans"
T_ACC = "public.section351_plan_accounts"
T_BASIS = "public.section351_basis_records"
T_STEP = "public.section351_tracker_steps"
T_XFER = "public.section351_transfers"
TASK_PLAN = "section351_plan_approval"
TASK_STEP = "section351_project_step"
SUBJECT_PLAN = "section351_plan"
SUBJECT_STEP = "section351_tracker_step"
DISCLOSURE_KEY = "section351.exit"
ZERO = Decimal(0)
STALE_VALUATION_DAYS = 5

OWNER_TYPES = ("individual", "joint", "trust", "s_corp", "partnership", "c_corp", "non_us", "retirement", "other")
OWNER_LABELS = {"individual": "Individual", "joint": "Joint", "trust": "Trust", "s_corp": "S corporation",
                "partnership": "Partnership / LLC", "c_corp": "C corporation", "non_us": "Non-U.S. person",
                "retirement": "Retirement / tax-deferred account", "other": "Other"}
ASSET_CLASSES = ("government", "restricted", "illiquid", "derivative", "crypto", "physical_commodity", "commodity_pool",
                 "grantor_trust", "partnership_interest")
ASSET_CLASS_LABELS = {"government": "Government security", "restricted": "Restricted security",
                      "illiquid": "Illiquid security", "derivative": "Derivative (option leg / short)",
                      "crypto": "Crypto asset", "physical_commodity": "Physical commodity",
                      "commodity_pool": "Commodity pool", "grantor_trust": "Grantor trust",
                      "partnership_interest": "Partnership interest"}
INELIGIBLE_DEFAULT = tuple(c for c in ASSET_CLASSES if c != "government")
DEFAULT_RULES = {"eligible_owner_types": ["individual", "joint", "trust", "s_corp"],
                 "review_owner_types": ["partnership"], "us_persons_only": True,
                 "ineligible_assets": list(INELIGIBLE_DEFAULT), "cash_rule": "not_contributed"}
CASH_RULE_LABELS = {"not_contributed": "Cash is not contributed — it stays in the account",
                    "contributed": "Cash is contributed (excluded from the 25 / 50 test's total)"}
PLAN_STATUS_LABELS = {"draft": "Draft", "ready": "Ready — every gate met", "pending_approval": "Awaiting approval",
                      "approved": "Approved — transfer instructions issued", "transferring": "Transferring",
                      "completed": "Completed", "cancelled": "Cancelled"}
TRANSFER_STATUS_LABELS = {"approved": "Approved — not yet sent", "sent": "Sent to the custodian",
                          "confirmed": "Confirmed — lot closed by transfer", "cancelled": "Cancelled"}
STEPS = (("counsel_engaged", "Counsel engaged", 14), ("sponsor_selected", "Sponsor / trust selected", 30),
         ("custodian_confirms", "Custodian confirms in-kind transfer", 60),
         ("valuation_date_set", "Valuation date set", 90), ("consents", "Consents collected", 150),
         ("basis_record_delivered", "Basis record delivered", 180), ("transfer", "Transfer", 210),
         ("confirmation", "Confirmation", 240))
STEP_LABELS = {k: v for k, v, _ in STEPS}
TIMELINE_NOTE = ("A Section 351 conversion typically runs 6–9 months from engaging counsel to the custodian's "
                 "confirmation — a one-time event. The default due dates are spread over 8 months; edit each.")
OPEN_ITEMS = (
    "The default section351.exit disclosure text (docs/disclosures/defaults/section351.exit.md) — counsel reviews it "
    "before it is published.",
    "Fees: the sponsor's fees and any adviser fee on the new ETF are not modelled here — counsel and the sponsor "
    "confirm them.",
    "Controlled groups (§1563(a)) count as one issuer; the master groups share classes by SEC CIK only.",
    "Government securities in the total but not as an issuer (Treas. Reg. §1.351-1(c)(6)(i)); cash excluded "
    "(§368(a)(2)(F)(iv)); fund look-through (§368(a)(2)(F)) — each reading to be confirmed by counsel.",
    "The per-lot split of ETF shares and tacked holding periods (Rev. Rul. 85-164) and asset-by-asset boot "
    "(Rev. Rul. 68-55).",
    "Retirement and tax-exempt accounts are refused (no gain to defer) — our rule, not the sponsor's.",
)


class Section351Error(ValueError):
    """A request the rules refuse (422)."""


class Section351NotFound(Section351Error, LookupError):
    """Not found in the caller's org (404)."""


class Section351Conflict(Section351Error):
    """The state does not allow it (409)."""


class NotReady(Section351Conflict):
    def __init__(self, message: str, gates: list[dict]):
        super().__init__(message)
        self.gates = gates


class InKindTransferNotDeclared(Section351Conflict):
    """The custodian adapter refuses: in_kind_transfers is not declared (the manual route is always offered)."""


class MakerCheckerViolation(PermissionError):
    """The maker may not decide their own plan (403)."""


def _uid(v, what: str) -> str:
    try:
        return str(uuid.UUID(str(v)))
    except (TypeError, ValueError):
        raise Section351NotFound(f"{what} not found") from None


def _s(v):
    return None if v is None else str(v)


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _audit(conn, org_id, user_id, action: str, resource_type: str, resource_id, payload: dict) -> None:
    await conn.execute(
        "INSERT INTO audit_log (org_id, user_id, action, resource_type, resource_id, payload) "
        "VALUES ($1::uuid, $2::uuid, $3, $4, $5::uuid, $6::jsonb)",
        str(org_id), _s(user_id), f"rep14.{action}", resource_type, str(resource_id), C.dumps(payload))


async def _human(conn, actor_id, org_id) -> dict:
    try:
        return await HT.assert_human(conn, actor_id, org_id)
    except HT.AgentActorRefused as exc:
        raise Section351Error(str(exc)) from exc


async def _update(conn, table: str, row_id, fields: dict) -> dict:
    try:
        return await C.update(conn, table, row_id, fields)
    except C.Rep10Conflict as exc:
        raise Section351Conflict(str(exc)) from exc


# ═══ Sponsor profiles (versioned, org admin) ════════════════════════════════════════════════════════════════════════
def _clean_rules(raw: dict) -> dict:
    out = {k: raw.get(k, v) for k, v in DEFAULT_RULES.items()}
    for k in ("eligible_owner_types", "review_owner_types"):
        vals = list(dict.fromkeys(out[k] or []))
        bad = [x for x in vals if x not in ("individual", "joint", "trust", "s_corp", "partnership")]
        if bad:
            raise Section351Error(f"{k}: {bad} — owner types a sponsor may admit are individual, joint, trust, "
                                  "s_corp and partnership (C-corps and non-U.S. persons are never eligible)")
        out[k] = vals
    if set(out["eligible_owner_types"]) & set(out["review_owner_types"]):
        raise Section351Error("an owner type is either eligible or needs sponsor review, not both")
    bad = [x for x in out["ineligible_assets"] or [] if x not in INELIGIBLE_DEFAULT]
    if bad:
        raise Section351Error(f"ineligible_assets: {bad} — one of {list(INELIGIBLE_DEFAULT)}")
    out["ineligible_assets"] = list(dict.fromkeys(out["ineligible_assets"] or []))
    if out["cash_rule"] not in CASH_RULE_LABELS:
        raise Section351Error(f"cash_rule is one of {list(CASH_RULE_LABELS)}")
    if not isinstance(out["us_persons_only"], bool):
        raise Section351Error("us_persons_only is true or false")
    return out


def _clean_windows(raw) -> list[dict]:
    out = []
    for w in raw or []:
        opens, closes = w.get("opens"), w.get("closes")
        if not isinstance(opens, date) or not isinstance(closes, date) or closes < opens:
            raise Section351Error("a launch window has an opening date and a closing date on or after it")
        out.append({"opens": opens.isoformat(), "closes": closes.isoformat(), "note": (w.get("note") or "")[:500]})
    return out


def profile_out(r: dict | None) -> dict | None:
    if r is None:
        return None
    return {"id": str(r["id"]), "profile_key": str(r["profile_key"]), "version_no": r["version_no"],
            "name": r["name"], "adviser_name": r["adviser_name"], "distributor_name": r["distributor_name"],
            "contact": r["contact"], "launch_windows": C.loads(r["launch_windows"]) or [],
            "eligible_owner_types": list(r["eligible_owner_types"]), "review_owner_types": list(r["review_owner_types"]),
            "us_persons_only": r["us_persons_only"], "ineligible_assets": list(r["ineligible_assets"]),
            "cash_rule": r["cash_rule"], "cash_rule_label": CASH_RULE_LABELS[r["cash_rule"]], "note": r["note"],
            "status": r["status"], "created_by": str(r["created_by"]), "created_at": C.iso(r["created_at"]),
            "live": r["valid_to"] is None}


async def list_profiles(conn, org_id, *, include_history: bool = False) -> list[dict]:
    rows = await conn.fetch(
        f"SELECT * FROM {T_PROFILE} WHERE org_id = $1::uuid AND system_to IS NULL "
        "AND ($2::bool OR valid_to IS NULL) ORDER BY name, profile_key, version_no DESC", str(org_id), include_history)
    return [profile_out(dict(r)) for r in rows]


async def profile_version(conn, org_id, profile_id) -> dict:
    r = await conn.fetchrow(f"SELECT * FROM {T_PROFILE} WHERE id = $1::uuid AND org_id = $2::uuid "
                            "AND system_to IS NULL", _uid(profile_id, "sponsor profile"), str(org_id))
    if r is None:
        raise Section351NotFound("sponsor profile not found")
    return dict(r)


async def save_profile(pool, *, org_id, actor_id, data: dict, profile_key=None) -> dict:
    """A new profile (version 1) or a NEW VERSION of ``profile_key`` (the live version is closed — rule 3)."""
    name = (data.get("name") or "").strip()
    if not name:
        raise Section351Error("a sponsor profile names the sponsor")
    rules = _clean_rules(data)
    windows = _clean_windows(data.get("launch_windows"))
    status = data.get("status") or "active"
    if status not in ("active", "retired"):
        raise Section351Error("status is active or retired")
    async with pool.acquire() as conn:
        actor = await _human(conn, actor_id, org_id)
        async with conn.transaction():
            if profile_key is None:
                key, n = uuid.uuid4(), 1
            else:
                key = _uid(profile_key, "sponsor profile")
                live = await conn.fetchrow(f"SELECT * FROM {T_PROFILE} WHERE org_id = $1::uuid AND profile_key = "
                                           "$2::uuid AND valid_to IS NULL AND system_to IS NULL FOR UPDATE",
                                           str(org_id), key)
                if live is None:
                    raise Section351NotFound("sponsor profile not found")
                n = live["version_no"] + 1
                await conn.execute(f"UPDATE {T_PROFILE} SET valid_to = clock_timestamp() WHERE id = $1", live["id"])
            row = await conn.fetchrow(
                f"""INSERT INTO {T_PROFILE} (org_id, profile_key, version_no, name, adviser_name, distributor_name,
                        contact, launch_windows, eligible_owner_types, review_owner_types, us_persons_only,
                        ineligible_assets, cash_rule, note, status, created_by, valid_from)
                    VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, $7, $8::jsonb, $9::text[], $10::text[], $11,
                            $12::text[], $13, $14, $15, $16::uuid, clock_timestamp()) RETURNING *""",
                str(org_id), str(key), n, name[:300], (data.get("adviser_name") or "").strip() or None,
                (data.get("distributor_name") or "").strip() or None, (data.get("contact") or "").strip() or None,
                C.dumps(windows), rules["eligible_owner_types"], rules["review_owner_types"], rules["us_persons_only"],
                rules["ineligible_assets"], rules["cash_rule"], (data.get("note") or "").strip() or None, status,
                str(actor["id"]))
            await _audit(conn, org_id, actor["id"], "sponsor_profile_saved", "section351_sponsor_profile", row["id"],
                         {"profile_key": str(key), "version_no": n, "name": name})
    return profile_out(dict(row))


# ═══ "Harvesting has run dry" thresholds (versioned per org) ════════════════════════════════════════════════════════
async def thresholds(conn, org_id) -> dict:
    r = await conn.fetchrow(f"SELECT * FROM {T_SET} WHERE org_id = $1::uuid AND valid_to IS NULL AND system_to IS NULL",
                            str(org_id))
    if r is None:
        return {**{k: str(v) for k, v in R.DEFAULT_THRESHOLDS.items()}, "version_no": None, "source": "default",
                "note": "the platform default (no version saved for this org)"}
    return {"max_harvestable_loss_pct": str(r["max_harvestable_loss_pct"]),
            "min_unrealized_gain_pct": str(r["min_unrealized_gain_pct"]),
            "min_years_since_inception": str(r["min_years_since_inception"]), "version_no": r["version_no"],
            "id": str(r["id"]), "source": "org", "note": r["note"], "saved_at": C.iso(r["created_at"]),
            "saved_by": str(r["created_by"])}


async def threshold_history(conn, org_id) -> list[dict]:
    return [{"version_no": r["version_no"], "max_harvestable_loss_pct": str(r["max_harvestable_loss_pct"]),
             "min_unrealized_gain_pct": str(r["min_unrealized_gain_pct"]),
             "min_years_since_inception": str(r["min_years_since_inception"]), "note": r["note"],
             "saved_at": C.iso(r["created_at"]), "live": r["valid_to"] is None}
            for r in await conn.fetch(f"SELECT * FROM {T_SET} WHERE org_id = $1::uuid AND system_to IS NULL "
                                      "ORDER BY version_no DESC", str(org_id))]


async def save_thresholds(pool, *, org_id, actor_id, max_harvestable_loss_pct: Decimal,
                          min_unrealized_gain_pct: Decimal, min_years_since_inception: Decimal,
                          note: str | None = None) -> dict:
    for v, lo, hi, what in ((max_harvestable_loss_pct, 0, 1, "the harvestable-loss threshold"),
                            (min_unrealized_gain_pct, 0, 10, "the unrealised-gain threshold"),
                            (min_years_since_inception, 0, 50, "the years-since-inception threshold")):
        if not isinstance(v, Decimal) or v < lo or v > hi:
            raise Section351Error(f"{what} is a decimal from {lo} to {hi}")
    async with pool.acquire() as conn:
        actor = await _human(conn, actor_id, org_id)
        async with conn.transaction():
            live = await conn.fetchrow(f"SELECT id, version_no FROM {T_SET} WHERE org_id = $1::uuid AND valid_to IS NULL "
                                       "AND system_to IS NULL FOR UPDATE", str(org_id))
            n = 1 if live is None else live["version_no"] + 1
            if live is not None:
                await conn.execute(f"UPDATE {T_SET} SET valid_to = clock_timestamp() WHERE id = $1", live["id"])
            row = await conn.fetchrow(
                f"""INSERT INTO {T_SET} (org_id, version_no, max_harvestable_loss_pct, min_unrealized_gain_pct,
                        min_years_since_inception, note, created_by, valid_from)
                    VALUES ($1::uuid, $2, $3, $4, $5, $6, $7::uuid, clock_timestamp()) RETURNING id""",
                str(org_id), n, max_harvestable_loss_pct, min_unrealized_gain_pct, min_years_since_inception,
                (note or "").strip() or None, str(actor["id"]))
            await _audit(conn, org_id, actor["id"], "thresholds_saved", "section351_settings", row["id"],
                         {"version_no": n})
        return await thresholds(conn, org_id)


# ═══ Asset classes the master does not carry ════════════════════════════════════════════════════════════════════════
async def add_asset_class(pool, *, org_id, actor_id, global_security_id, asset_class: str, source: str, as_of: date,
                          platform: bool = False) -> dict:
    if asset_class not in ASSET_CLASSES:
        raise Section351Error(f"asset_class is one of {list(ASSET_CLASSES)}")
    if not (source or "").strip() or not isinstance(as_of, date):
        raise Section351Error("a classification carries its source and as-of date")
    sid = _uid(global_security_id, "security")
    async with pool.acquire() as conn:
        actor = await _human(conn, actor_id, org_id)
        if not await conn.fetchval("SELECT 1 FROM portfolio.securities_global WHERE id = $1::uuid AND system_to IS NULL "
                                   "LIMIT 1", sid):
            raise Section351NotFound("security not found")
        async with conn.transaction():
            row = await conn.fetchrow(
                f"""INSERT INTO {T_CLASS} (owner_scope, org_id, global_security_id, asset_class, source, as_of,
                        entered_by) VALUES ($1, $2::uuid, $3::uuid, $4, $5, $6, $7::uuid) RETURNING *""",
                "platform" if platform else "org", None if platform else str(org_id), sid, asset_class,
                source.strip()[:500], as_of, str(actor["id"]))
            await _audit(conn, org_id, actor["id"], "asset_class_added", "section351_asset_class", row["id"],
                         {"security_id": sid, "asset_class": asset_class, "scope": row["owner_scope"]})
    return {"id": str(row["id"]), "scope": row["owner_scope"], "global_security_id": sid, "asset_class": asset_class,
            "label": ASSET_CLASS_LABELS[asset_class], "source": row["source"], "as_of": C.iso(row["as_of"])}


async def retire_asset_class(pool, *, org_id, actor_id, class_id) -> dict:
    async with pool.acquire() as conn:
        actor = await _human(conn, actor_id, org_id)
        async with conn.transaction():
            r = await conn.fetchrow(f"UPDATE {T_CLASS} SET valid_to = clock_timestamp() WHERE id = $1::uuid "
                                    "AND valid_to IS NULL AND system_to IS NULL RETURNING id, owner_scope",
                                    _uid(class_id, "classification"))
            if r is None:
                raise Section351NotFound("classification not found")
            await _audit(conn, org_id, actor["id"], "asset_class_retired", "section351_asset_class", r["id"], {})
    return {"id": str(r["id"]), "retired": True}


async def security_facts(conn, org_id, keys: list[str]) -> dict[str, dict]:
    """Per security: name, type, the issuer key (SEC CIK → 'cik:<n>' groups share classes; none → its own id), fund
    or not, the entered asset classes (platform + the org's, live) and whether it is an option contract."""
    keys = [str(k) for k in dict.fromkeys(keys) if k]
    if not keys:
        return {}
    rows = await conn.fetch(
        """SELECT g.id, g.name, g.security_type,
                  (SELECT min(a.value) FROM portfolio.security_reference_attributes a WHERE a.global_security_id = g.id
                     AND a.field = 'cik' AND a.valid_to IS NULL AND a.system_to IS NULL) AS cik,
                  (SELECT bool_or(lower(a.value) = 'true') FROM portfolio.security_reference_attributes a
                    WHERE a.global_security_id = g.id AND a.field = 'is_etf' AND a.valid_to IS NULL
                      AND a.system_to IS NULL) AS is_etf,
                  (SELECT id_value FROM portfolio.securities_global_identifiers t WHERE t.global_security_id = g.id
                     AND t.id_type = 'ticker' AND t.system_to IS NULL AND t.valid_to IS NULL LIMIT 1) AS ticker
           FROM portfolio.securities_global g WHERE g.id = ANY($1::uuid[]) AND g.valid_to IS NULL
             AND g.system_to IS NULL""", keys)
    classes: dict[str, set] = {}
    for r in await conn.fetch(
            f"SELECT global_security_id, asset_class FROM {T_CLASS} WHERE global_security_id = ANY($1::uuid[]) "
            "AND valid_to IS NULL AND system_to IS NULL AND (owner_scope = 'platform' OR org_id = $2::uuid)",
            keys, str(org_id)):
        classes.setdefault(str(r["global_security_id"]), set()).add(r["asset_class"])
    out = {}
    for r in rows:
        k = str(r["id"])
        cik = (r["cik"] or "").lstrip("0") or None
        cl = set(classes.get(k, set()))
        if r["security_type"] == "option":
            cl.add("derivative")
        label = f"{r['name']} ({r['ticker']})" if r["ticker"] else r["name"]
        out[k] = {"label": label, "name": r["name"], "type": r["security_type"], "cik": cik,
                  "issuer_key": f"cik:{cik}" if cik else f"security:{k}", "issuer_label": r["name"],
                  "fund": r["security_type"] == "fund" or bool(r["is_etf"]), "classes": sorted(cl),
                  "government": "government" in cl}
    return out


# ═══ Accounts: owner type, eligibility, lot-level data ═════════════════════════════════════════════════════════════
async def owner_type_of(conn, org_id, account: dict) -> tuple[str, str]:
    """(owner type, how it was read) from the account's registration and its primary entity."""
    ent = await conn.fetchrow("SELECT entity_type::text AS t, country_code FROM public.entities WHERE id = $1::uuid "
                              "AND system_to IS NULL ORDER BY valid_to NULLS FIRST LIMIT 1",
                              str(account["primary_entity_id"]))
    et = (ent["t"] if ent else None) or ""
    reg = (account.get("registration_type") or "").lower()
    country = ((ent["country_code"] if ent else None) or "").upper()
    if et.startswith("corp_") or (country and country not in ("US", "USA")):
        return "non_us", f"the member's entity is {et or 'recorded'} with country {country or '—'}"
    if reg in ("ira", "roth_ira", "401k_rollover", "529") or (account.get("tax_status") or "") in (
            "tax_deferred", "tax_exempt"):
        return "retirement", f"registration {reg}, tax status {account.get('tax_status')}"
    if et in ("c_corp", "corporation"):
        return "c_corp", f"the member's entity type is {et}"
    if et == "s_corp":
        return "s_corp", "the member's entity type is s_corp"
    if reg in ("llc", "lp") or et in ("llc", "lp", "gp"):
        return "partnership", f"registration {reg}, entity type {et}"
    if reg == "trust" or et == "trust":
        return "trust", f"registration {reg}, entity type {et}"
    if reg == "individual":
        owners = await conn.fetchval("SELECT count(DISTINCT entity_id) FROM public.account_owners WHERE account_id = "
                                     "$1::uuid", str(account["id"])) or 0
        return ("joint", f"registration individual with {owners} owners") if owners >= 2 else \
            ("individual", "registration individual")
    return "other", f"registration {reg or '—'}, entity type {et or '—'}"


def owner_decision(owner_type: str, profile: dict) -> dict:
    if owner_type in profile["eligible_owner_types"]:
        return {"decision": "eligible", "reason": f"{OWNER_LABELS[owner_type]} — eligible under the sponsor profile"}
    if owner_type in profile["review_owner_types"]:
        return {"decision": "needs_sponsor_review",
                "reason": f"{OWNER_LABELS[owner_type]} — needs the sponsor's review before it can be ready"}
    why = {"c_corp": "a C corporation is not eligible", "non_us": "a non-U.S. person is not eligible",
           "retirement": "a retirement or tax-exempt account has no gain to defer — not eligible"}.get(
        owner_type, f"{OWNER_LABELS.get(owner_type, owner_type)} is not an eligible owner type under the sponsor "
                    "profile")
    return {"decision": "refused", "reason": why}


async def account_screen(conn, org_id, account: dict, today: date | None = None) -> dict:
    """Task 2 — one account: eligible or not (each reason), and the candidate metrics from ITS OWN valuation."""
    from services import account_models as AM
    from services import authority as AU
    from services import lot_method as LM

    today = today or date.today()
    reasons = []
    link = await AM.active_link(conn, account["id"])
    if link is None:
        reasons.append({"code": "not_replica", "reason": "does not follow a replica model"})
    if account.get("is_held_away"):
        reasons.append({"code": "held_away", "reason": "held-away account — Hollisworks never instructs a transfer "
                                                      "from it"})
    eff = AU.effective(account)
    if eff["authority"] != "managed":
        reasons.append({"code": "authority", "reason": f"authority {AU.AUTHORITY_SHORT[eff['authority']]} — a Section "
                                                      "351 contribution needs 'managed'"})
    if AM.is_closed(account, today):
        reasons.append({"code": "closed", "reason": "the account is closed"})
    hold = await AM.load_holdings(conn, org_id, account["id"])
    method, _ = await LM.lot_method_for(conn, org_id, account, today)
    no_lots = sorted(h.label for h in hold["holdings"]
                     if h.key in method.average_cost or not h.lots or any(lot.id is None for lot in h.lots))
    if no_lots:
        reasons.append({"code": "average_cost", "reason": "average cost only (no lot-level basis) for "
                                                          f"{', '.join(no_lots[:5])}" + ("…" if len(no_lots) > 5 else "")
                        + " — tax lots from the custodian are required"})
    undated = sorted({h.label for h in hold["holdings"] if h.key not in method.average_cost
                      for lot in h.lots if lot.id is not None and (lot.acquired is None or lot.unit_cost is None)})
    if undated:
        reasons.append({"code": "lot_dates", "reason": "lots without an acquisition date or basis for "
                                                       f"{', '.join(undated[:5])}" + ("…" if len(undated) > 5 else "")
                        + " — the basis record carries each lot's basis and date (tacked holding period)"})
    if not hold["holdings"]:
        reasons.append({"code": "no_holdings", "reason": "no holdings on record"})
    m = metrics(hold, today)
    start = await conn.fetchval("SELECT min(valid_from) FROM public.account_model_links WHERE account_id = $1::uuid "
                                "AND status IN ('active', 'ended') AND system_to IS NULL", str(account["id"]))
    years = R.years_between(start.date() if start else None, today)
    m["years_since_inception"] = None if years is None else str(years)
    m["inception"] = start.date().isoformat() if start else None
    m["decay"] = await benefit_decay(conn, org_id, account, start.date() if start else None, today)
    return {"account_id": str(account["id"]), "account": account["account_number_masked"],
            "custodian_code": account.get("custodian_code"), "authority": eff["authority"],
            "eligible": not reasons, "reasons": reasons, "metrics": m, "model_id": _s(link["model_id"]) if link else None,
            "link_id": _s(link["id"]) if link else None, "valuation_as_of": C.iso(hold["valuation_as_of"])}


def metrics(hold: dict, today: date) -> dict:
    value = gain_value = gain = loss = ZERO
    unpriced = 0
    with localcontext() as ctx:
        ctx.prec = 60
        for h in hold["holdings"]:
            if h.price is None or h.market_value is None:
                unpriced += 1
                continue
            value += h.market_value
            for lot in h.lots:
                if lot.unit_cost is None:
                    continue
                g = (h.price - lot.unit_cost) * lot.quantity
                if g > 0:
                    gain += g
                    gain_value += h.price * lot.quantity
                elif g < 0:
                    loss += -g
        total = value + (hold["cash"] or ZERO)
    return {"account_value": str(R.money(total)), "securities_value": str(R.money(value)),
            "cash": str(R.money(hold["cash"] or ZERO)), "appreciated_value": str(R.money(gain_value)),
            "unrealized_gain": str(R.money(gain)), "harvestable_loss": str(R.money(loss)),
            "net_unrealized": str(R.money(gain - loss)), "unpriced_holdings": unpriced, "label": "estimate",
            "_total": total, "_gain": gain, "_loss": loss}


async def benefit_decay(conn, org_id, account: dict, start: date | None, today: date) -> dict:
    """Shown next to year 1 (the guardrail): losses harvested in the link's first tax year vs this tax year, from the
    rep6 loss bank (approved harvest proposals). decay = 1 − this year ÷ year 1. An estimate."""
    from services import loss_bank as LB

    if start is None:
        return {"estimated": False, "note": "not estimated — no model link start on record", "label": "estimate"}
    y1 = await LB.harvested(conn, org_id, [str(account["id"])], start.year)
    now = await LB.harvested(conn, org_id, [str(account["id"])], today.year)
    a, b = y1["st"] + y1["lt"], now["st"] + now["lt"]
    decay = None
    if a > 0:
        with localcontext() as ctx:
            ctx.prec = 40
            decay = ONE_MINUS(b / a)
    return {"estimated": a > 0, "year_1": start.year, "year_1_harvested": str(R.money(a)),
            "this_year": today.year, "this_year_harvested": str(R.money(b)),
            "decay": None if decay is None else str(R.qw(decay)), "decay_pct": None if decay is None else
            R.pct_text(decay), "label": "estimate",
            "note": LB.DECAY_NOTE if a > 0 else "not estimated — no harvested losses in year 1"}


def ONE_MINUS(x: Decimal) -> Decimal:  # noqa: N802 — reads as the formula
    return Decimal(1) - x


async def candidates(conn, org_id, *, today: date | None = None) -> dict:
    """Task 2 — every open account of the org: eligible replica accounts with their metrics and the run-dry flag
    (the org's thresholds, versioned), then every ineligible account with its reasons."""
    from services import account_models as AM

    today = today or date.today()
    th = await thresholds(conn, org_id)
    rows = await conn.fetch("SELECT id FROM public.accounts WHERE org_id = $1::uuid AND system_to IS NULL "
                            "AND valid_to IS NULL AND (closed_on IS NULL OR closed_on > $2) "
                            "ORDER BY account_number_masked, id", str(org_id), today)
    eligible, ineligible = [], []
    for r in rows:
        acct = await AM.load_account(conn, org_id, r["id"])
        sc = await account_screen(conn, org_id, acct, today)
        m = sc["metrics"]
        yrs = Decimal(m["years_since_inception"]) if m["years_since_inception"] is not None else None
        sc["run_dry"] = R.run_dry(account_value=m["_total"], harvestable_loss=m["_loss"], unrealized_gain=m["_gain"],
                                  years_since_inception=yrs, thresholds=th)
        for k in ("_total", "_gain", "_loss"):
            m.pop(k, None)
        (eligible if sc["eligible"] else ineligible).append(sc)
    eligible.sort(key=lambda s: (not s["run_dry"]["run_dry"], -Decimal(s["metrics"]["unrealized_gain"]),
                                 s["account"]))
    return {"as_of": today.isoformat(), "thresholds": th, "eligible": eligible, "ineligible": ineligible,
            "rule": "harvesting has run dry when the harvestable loss remaining is at most the threshold share of the "
                    "account, the unrealised gain at least its threshold share, and the model link at least the "
                    "threshold years old (all three)", "label": "estimate"}


# ═══ Task 3 — the contribution and its 25 / 50 test ═══════════════════════════════════════════════════════════════
async def contribution(conn, org_id, account: dict, *, valuation_date: date, profile: dict,
                       lot_ids: list[str] | None = None, today: date | None = None) -> dict:
    """The account's contribution at ``valuation_date`` (all eligible lots, or ``lot_ids``) and its test. One read;
    the test itself is :func:`services.section351_rules.diversification_test` (pure)."""
    from services import account_models as AM
    from services import lot_method as LM
    from services import replica_holdings as RH

    today = today or date.today()
    hold = await AM.load_holdings(conn, org_id, account["id"])
    method, _ = await LM.lot_method_for(conn, org_id, account, today)
    want = {str(x) for x in lot_ids} if lot_ids else None
    held_ids = {str(lot.id) for h in hold["holdings"] for lot in h.lots if lot.id}
    if want and not want <= held_ids:
        raise Section351Error(f"lot(s) {sorted(want - held_ids)} are not open lots of this account")
    facts = await security_facts(conn, org_id, [h.key for h in hold["holdings"]])
    asset_types = {str(r["id"]): (r["asset_type"] or "").lower() for r in await conn.fetch(
        "SELECT id, asset_type FROM portfolio.assets WHERE id = ANY($1::uuid[])",
        [a for h in hold["holdings"] for a in h.asset_ids])}
    lines: list[R.Line] = []
    excluded, blocked, warnings, lots_out = [], [], [], []
    ineligible = set(profile["ineligible_assets"])
    for h in hold["holdings"]:
        f = facts.get(h.key) or {"label": h.label, "classes": [], "fund": False, "issuer_key": f"security:{h.key}",
                                 "issuer_label": h.label, "government": False}
        chosen = [lot for lot in h.lots if lot.id and (want is None or str(lot.id) in want)]
        if want is not None and not chosen:
            continue
        cls = set(f["classes"])
        if any(asset_types.get(a) == "digital_asset" for a in h.asset_ids):
            cls.add("crypto")
        hit = sorted(cls & ineligible)
        if hit:
            excluded.append({"security_id": h.key, "label": f["label"], "classes": hit,
                             "reason": f"ineligible asset ({', '.join(ASSET_CLASS_LABELS[c] for c in hit)}) under the "
                                       "sponsor profile — excluded and stays in the account",
                             "value": None if h.market_value is None else str(R.money(h.market_value))})
            continue
        if h.key in method.average_cost or not chosen or any(lot.id is None for lot in h.lots):
            blocked.append(f"{f['label']}: average cost only — lot-level basis is required (fails closed)")
            continue
        if h.price is None:
            blocked.append(f"{f['label']}: needs a price — it cannot be valued (fails closed)")
            continue
        q = sum((lot.quantity for lot in chosen), ZERO)
        value = q * h.price
        src = ("price — end-of-day close (internal use)" if h.price_source == "security_price" else
               "entered value — the account's own position (custodian / entered)")
        if h.price_as_of and (h.price_as_of > valuation_date
                              or (valuation_date - h.price_as_of).days > STALE_VALUATION_DAYS):
            warnings.append({"code": "valuation_date", "message": f"{f['label']}: valued as of "
                             f"{h.price_as_of.isoformat()}, not the plan's valuation date {valuation_date.isoformat()}"})
        for lot in chosen:
            lots_out.append({"tax_lot_id": str(lot.id), "security_id": h.key, "label": f["label"],
                             "quantity": str(lot.quantity), "unit_cost": None if lot.unit_cost is None else
                             str(lot.unit_cost), "basis": None if lot.unit_cost is None else
                             str(lot.unit_cost * lot.quantity), "acquired_date": C.iso(lot.acquired),
                             "fmv": str(lot.quantity * h.price), "price": str(h.price),
                             "price_as_of": C.iso(h.price_as_of), "valuation_source": src})
            if lot.unit_cost is None or lot.acquired is None:
                blocked.append(f"{f['label']}: lot {lot.id} has no basis or acquisition date — the basis record "
                               "needs both (fails closed)")
        if f["fund"]:
            snap = await RH.latest_snapshot(conn, h.key)
            rows = await RH.snapshot_rows(conn, snap["id"]) if snap else []
            cfacts = await security_facts(conn, org_id, [str(r["constituent_global_security_id"]) for r in rows
                                                         if r.get("constituent_global_security_id")])
            lt, why = R.look_through(fund_id=h.key, fund_label=f["label"], fund_value=value, snapshot=snap, rows=rows,
                                     issuers=cfacts, valuation_source=src, valuation_as_of=h.price_as_of)
            if why:
                blocked.append(why)
            for x in lt:
                x.lot_ids = [str(lot.id) for lot in chosen]
            lines += lt
            continue
        kind = R.KIND_GOVERNMENT if f["government"] else R.KIND_ISSUER
        lines.append(R.Line(security_id=h.key, label=f["label"], value=value, kind=kind,
                            issuer_key=None if f["government"] else f["issuer_key"],
                            issuer_label=None if f["government"] else f["issuer_label"], via="direct",
                            valuation_source=src, valuation_as_of=h.price_as_of,
                            lot_ids=[str(lot.id) for lot in chosen]))
    for u in hold["unresolved"]:
        excluded.append({"security_id": None, "label": u["label"], "classes": [],
                         "reason": "not in the security master — cannot be tested or transferred by lot; excluded"})
    cash = hold["cash"] or ZERO
    cash_note = CASH_RULE_LABELS[profile["cash_rule"]]
    if profile["cash_rule"] == "contributed" and want is None and cash > 0:
        lines.append(R.Line(security_id="cash", label="Cash", value=cash, kind=R.KIND_CASH, via="direct",
                            valuation_source="cash balance", valuation_as_of=hold["cash_as_of"]))
    test = R.diversification_test(lines, blocked=blocked)
    contributed = sum((x.value for x in lines), ZERO)
    test.update({"account_id": str(account["id"]), "account": account["account_number_masked"],
                 "valuation_date": valuation_date.isoformat(), "excluded": excluded, "warnings": warnings,
                 "lots": lots_out, "contributed_value": str(R.money(contributed)),
                 "contributed_value_exact": str(contributed), "cash_in_account": str(R.money(cash)),
                 "cash_rule": profile["cash_rule"], "cash_note": cash_note,
                 "sponsor_profile": {"id": str(profile["id"]), "version_no": profile["version_no"]}})
    return test


async def test_account(conn, org_id, account_id, *, valuation_date: date, profile_id, lot_ids=None) -> dict:
    """Task 3 as a stand-alone read (no plan): the account at a valuation date under a sponsor profile version."""
    from services import account_models as AM

    acct = await AM.load_account(conn, org_id, account_id)
    prof = await profile_version(conn, org_id, profile_id)
    return await contribution(conn, org_id, acct, valuation_date=valuation_date, profile=prof, lot_ids=lot_ids)


# ═══ Task 4 — the fix-up proposal (rep5 proposal + maker-checker; nothing executes here) ══════════════════════════
async def fixup(pool, *, org_id, actor_id, account_id, valuation_date: date, profile_id,
                today: date | None = None) -> dict:
    """A failing account → the least-tax sales (rep6 tax per dollar at the profile's rates, specific lots) from the
    over-weight issuers, the proceeds into the account's model names; written as a NORMAL rep5 proposal (trigger
    manual, a person is the maker, lot method 'specific' with the picked lots) that goes through rep5's
    maker-checker. Its tax cost is the ENGINE's estimate (the proposal's ST / LT gains at the profile's rates)."""
    from services import account_models as AM
    from services import lot_method as LM
    from services import rebalance_engine as E
    from services import rebalance_proposals as RP
    from services import tax_profiles as TP

    today = today or date.today()
    async with pool.acquire() as conn:
        actor = await _human(conn, actor_id, org_id)
        acct = await AM.load_account(conn, org_id, account_id)
        sc = await account_screen(conn, org_id, acct, today)
        if not sc["eligible"]:
            raise Section351Conflict("the account is not eligible: " + "; ".join(r["reason"] for r in sc["reasons"]))
        prof = await profile_version(conn, org_id, profile_id)
        before = await contribution(conn, org_id, acct, valuation_date=valuation_date, profile=prof, today=today)
        if before["verdict"] == "pass":
            raise Section351Conflict("the account already passes the 25 / 50 test — no fix-up is needed")
        if before["verdict"] == "holdings_needed":
            raise Section351Conflict("the test cannot run (" + "; ".join(before["blocked"]) + ") — fix the data first")
        link = await AM.active_link(conn, acct["id"])
        model = await AM.model_brief(conn, org_id, link["model_id"])
        tg = await RP.target_for(conn, org_id, link, model["live_version_id"], today=today)
        hold = await AM.load_holdings(conn, org_id, acct["id"])
        held = {h.key: h for h in hold["holdings"]}
        direct = [ln for ln in before["lines"] if ln["via"] == "direct" and ln["kind"] == R.KIND_ISSUER]
        facts = await security_facts(conn, org_id, [x["security_id"] for x in direct] +
                                     [k for k in tg["target"] if k != E.CASH])
        fixed_issuers: dict[str, Decimal] = {}
        non_issuer = ZERO
        for ln in before["lines"]:
            if ln["via"] == "look_through" and ln["kind"] == R.KIND_ISSUER:
                fixed_issuers[ln["issuer_key"]] = fixed_issuers.get(ln["issuer_key"], ZERO) + Decimal(ln["value_exact"])
            elif ln["kind"] in (R.KIND_GOVERNMENT, R.KIND_OTHER):
                non_issuer += Decimal(ln["value_exact"])
        fh = []
        for ln in direct:
            h = held[ln["security_id"]]
            fh.append(R.FixHolding(security_id=h.key, label=ln["label"], issuer_key=ln["issuer_key"],
                                   issuer_label=ln["issuer_label"], price=h.price, quantity=h.quantity,
                                   lots=[R.FixLot(str(x.id), x.quantity, x.unit_cost, x.acquired, x.term_hint)
                                         for x in h.lots if x.id]))
        unheld = [k for k in tg["target"] if k != E.CASH and k not in held]
        px = await RP._unheld_prices(conn, unheld, today - timedelta(days=RP.DEFAULT_STALE_DAYS), org_id=org_id,
                                     today=today)
        buys = []
        for k, w in sorted(tg["target"].items()):
            if k == E.CASH or w <= 0:
                continue
            p = held[k].price if k in held and held[k].price is not None else (px[k][0] if k in px else None)
            if p is None or p <= 0 or (facts.get(k) or {}).get("government"):
                continue
            buys.append({"security_id": k, "label": (facts.get(k) or {}).get("label") or tg["labels"].get(k, k),
                         "issuer_key": (facts.get(k) or {}).get("issuer_key") or f"security:{k}", "price": p,
                         "weight": w})
        rates = TP.combined_rates(await TP.profile_for_account(conn, org_id, acct, today.year))
        plan = R.fixup_plan(fh, buys, on=today, rates=rates, fixed={"issuers": fixed_issuers,
                                                                    "non_issuer": non_issuer})
        if not plan["possible"] and not plan["sales"]:
            raise Section351Conflict(f"no fix-up found: {plan['reason']}")
        # the target the engine reproduces: each sale / buy sized half a share inside its floor (engine floors)
        A, cur_w = E.current_weights(hold["holdings"], hold["cash"])
        sold = {}
        for s in plan["sales"]:
            sold[s["security_id"]] = sold.get(s["security_id"], ZERO) + Decimal(s["quantity"])
        got = {b["security_id"]: Decimal(b["quantity"]) for b in plan["buys"]}
        price = {b["security_id"]: Decimal(str(b["price"])) for b in buys}
        price.update({h.key: h.price for h in hold["holdings"] if h.price is not None})
        target = dict(cur_w)
        with localcontext() as ctx:
            ctx.prec = 60
            for k, q in sold.items():
                target[k] = E.qw(max(ZERO, held[k].market_value - (q + Decimal("0.5")) * price[k]) / A)
            for k, q in got.items():
                cur = held[k].market_value if k in held and held[k].market_value is not None else ZERO
                target[k] = E.qw((cur + (q + Decimal("0.5")) * price[k]) / A)
            # the cash target leaves EXACTLY the buys' (q + ½) × price available, so the engine never scales them
            # (available = cash + proceeds − cash target; proceeds = Σ sold q × price)
            proceeds = sum((q * price[k] for k, q in sold.items()), ZERO)
            need = sum(((q + Decimal("0.5")) * price[k] for k, q in got.items()), ZERO)
            target[E.CASH] = E.qw(max(ZERO, hold["cash"] + proceeds - need) / A)
        labels = {**tg["labels"], **{h.key: h.label for h in hold["holdings"]},
                  **{b["security_id"]: b["label"] for b in buys}}
        override = LM.clean_override({"lot_method": "specific", "lots": [
            {"global_security_id": s["security_id"], "lot_id": s["lot_id"], "quantity": Decimal(s["quantity"])}
            for s in plan["sales"]]})
    ref = {"section351": {"valuation_date": valuation_date.isoformat(), "sponsor_profile_id": str(prof["id"]),
                          "before": {"verdict": before["verdict"], "largest": before["largest_issuer_pct"],
                                     "top_five": before["top_five_pct"]},
                          "plan_sales": plan["sales"], "plan_buys": plan["buys"]}}
    p = await RP.generate(pool, org_id=org_id, account_id=acct["id"], trigger="manual", maker_kind="user",
                          maker_id=actor["id"], trigger_ref=ref, today=today, lot_override=override,
                          target_override={"target": target, "labels": labels, "excluded": {}, "notes": [],
                                           "warnings": [{"code": "section351_fixup",
                                                         "message": "Section 351 fix-up: sells the over-weight "
                                                                    "issuers' least-tax lots so the contribution "
                                                                    "passes the 25 / 50 test"}],
                                           "alternates": {}})
    async with pool.acquire() as conn:
        trades = await RP.trades_of(conn, p["id"])
        after = await _after_test(conn, org_id, acct, before, trades, held, price, facts)
    st = Decimal(str(p["est_st_gain"]))
    lt = Decimal(str(p["est_lt_gain"]))
    matches = _trades_match(plan, trades)
    return {"proposal_id": str(p["id"]), "proposal_status": p["status"], "before": _brief(before),
            "plan": plan, "trades": [RP.trade_out(t) for t in trades], "matches_plan": matches,
            "after": _brief(after), "after_test": after,
            "tax": R.estimated_tax(st, lt, rates), "rates": TP.rates_out(rates),
            "tax_source": "the rep6 engine's estimate on the proposal (its short- and long-term gains at the tax "
                          "profile's combined rates)",
            "note": ("the engine's trades are the plan's" if matches else
                     "the engine's trades differ from the plan (gain budget, cash floor or short-term guard) — the "
                     "test after uses the engine's trades"), "label": R.LABEL,
            "approval": "the proposal goes through the normal rep5 maker-checker — a different holder of "
                        "approve_proposals approves it; nothing executes here"}


def _brief(t: dict) -> dict:
    return {"verdict": t["verdict"], "largest_issuer_pct": t["largest_issuer_pct"], "top_five_pct": t["top_five_pct"],
            "total_assets": t["total_assets"]}


def _trades_match(plan: dict, trades: list[dict]) -> bool:
    want = {(s["security_id"], "sell") for s in plan["sales"]} | {(b["security_id"], "buy") for b in plan["buys"]}
    have = {(str(t["global_security_id"]), t["side"]) for t in trades}
    if want != have:
        return False
    q = {}
    for s in plan["sales"]:
        q[(s["security_id"], "sell")] = q.get((s["security_id"], "sell"), ZERO) + Decimal(s["quantity"])
    for b in plan["buys"]:
        q[(b["security_id"], "buy")] = Decimal(b["quantity"])
    return all(t["quantity"] is not None and Decimal(str(t["quantity"])) == q[(str(t["global_security_id"]), t["side"])]
               for t in trades)


async def _after_test(conn, org_id, acct: dict, before: dict, trades: list[dict], held: dict, price: dict,
                      facts: dict) -> dict:
    """The test on the holdings AFTER the proposal's own trades (as stored), at the same prices."""
    qty = {k: h.quantity for k, h in held.items()}
    for t in trades:
        k = str(t["global_security_id"])
        q = Decimal(str(t["quantity"] or 0))
        qty[k] = qty.get(k, ZERO) + (q if t["side"] == "buy" else -q)
        if k not in price and t.get("price") is not None:
            price[k] = Decimal(str(t["price"]))
    more = await security_facts(conn, org_id, [k for k in qty if k not in facts])
    facts = {**facts, **more}
    # unchanged by the trades: every look-through line (the funds are not traded here) and any direct cash line
    lines = [R.Line(security_id=ln["security_id"], label=ln["label"], value=Decimal(ln["value_exact"]), kind=ln["kind"],
                    issuer_key=ln["issuer_key"], issuer_label=ln["issuer_label"], via=ln["via"])
             for ln in before["lines"] if ln["via"] == "look_through" or ln["kind"] == R.KIND_CASH]
    direct_keys = {ln["security_id"] for ln in before["lines"] if ln["via"] == "direct"}
    for k, q in sorted(qty.items()):
        if q <= 0 or k not in price:
            continue
        if k not in direct_keys and not any(str(t["global_security_id"]) == k and t["side"] == "buy" for t in trades):
            continue
        f = facts.get(k) or {"label": k, "issuer_key": f"security:{k}", "issuer_label": k, "government": False}
        lines.append(R.Line(security_id=k, label=f["label"], value=q * price[k],
                            kind=R.KIND_GOVERNMENT if f.get("government") else R.KIND_ISSUER,
                            issuer_key=None if f.get("government") else f["issuer_key"],
                            issuer_label=None if f.get("government") else f["issuer_label"], via="direct"))
    return R.diversification_test(lines)


# ═══ Plans (the pool) ═══════════════════════════════════════════════════════════════════════════════════════════════
def plan_out(r: dict) -> dict:
    return {"id": str(r["id"]), "name": r["name"], "target_etf": r["target_etf"], "sponsor_trust": r["sponsor_trust"],
            "sponsor_profile_id": str(r["sponsor_profile_id"]), "sponsor_profile_version": r["sponsor_profile_version"],
            "valuation_date": C.iso(r["valuation_date"]), "seed_capital": str(r["seed_capital"]),
            "seed_note": r["seed_note"], "etf_custodian_account": r["etf_custodian_account"], "status": r["status"],
            "status_label": PLAN_STATUS_LABELS[r["status"]], "control_result": C.loads(r["control_result"]) or {},
            "readiness": C.loads(r["readiness"]) or {}, "maker_id": str(r["maker_id"]),
            "submitted_by": _s(r["submitted_by"]), "approved_by": _s(r["approved_by"]),
            "approved_at": C.iso(r["approved_at"]), "approval_task_id": _s(r["approval_task_id"]),
            "decision_note": r["decision_note"], "ready_at": C.iso(r["ready_at"]),
            "completed_at": C.iso(r["completed_at"]), "cancel_reason": r["cancel_reason"],
            "created_at": C.iso(r["created_at"])}


def account_out(r: dict) -> dict:
    return {"id": str(r["id"]), "plan_id": str(r["plan_id"]), "account_id": str(r["account_id"]),
            "owner_type": r["owner_type"], "owner_label": OWNER_LABELS[r["owner_type"]],
            "eligibility": C.loads(r["eligibility"]) or {}, "us_person": r["us_person"],
            "us_person_source": r["us_person_source"], "us_person_as_of": C.iso(r["us_person_as_of"]),
            "lot_ids": [str(x) for x in r["lot_ids"]] if r["lot_ids"] else None,
            "test_verdict": r["test_verdict"], "test_verdict_label": R.VERDICT_LABELS[r["test_verdict"]],
            "test_result": C.loads(r["test_result"]) or {},
            "contributed_value": None if r["contributed_value"] is None else str(r["contributed_value"]),
            "consent": None if r["consent_document_id"] is None else {
                "document_id": str(r["consent_document_id"]), "signed_on": C.iso(r["consent_signed_on"]),
                "signer": r["consent_signer"], "recorded_by": _s(r["consent_recorded_by"])},
            "tax_adviser": None if r["tax_adviser_confirmed_on"] is None else {
                "name": r["tax_adviser_name"], "confirmed_on": C.iso(r["tax_adviser_confirmed_on"]),
                "note": r["tax_adviser_note"], "recorded_by": _s(r["tax_adviser_recorded_by"])},
            "cash_to_member": str(r["cash_to_member"]), "status": r["status"],
            "etf_global_security_id": _s(r["etf_global_security_id"]),
            "etf_shares": None if r["etf_shares"] is None else str(r["etf_shares"]),
            "received_on": C.iso(r["received_on"])}


async def load_plan(conn, org_id, plan_id, *, lock: bool = False) -> dict:
    r = await conn.fetchrow(f"SELECT * FROM {T_PLAN} WHERE id = $1::uuid AND org_id = $2::uuid AND system_to IS NULL"
                            + (" FOR UPDATE" if lock else ""), _uid(plan_id, "plan"), str(org_id))
    if r is None:
        raise Section351NotFound("plan not found")
    return dict(r)


async def plan_accounts(conn, plan_id, *, include_removed: bool = False) -> list[dict]:
    return [dict(r) for r in await conn.fetch(
        f"SELECT * FROM {T_ACC} WHERE plan_id = $1::uuid AND system_to IS NULL AND valid_to IS NULL "
        "AND ($2::bool OR status <> 'removed') ORDER BY created_at, id", str(plan_id), include_removed)]


async def load_plan_account(conn, org_id, plan_account_id) -> dict:
    r = await conn.fetchrow(f"SELECT * FROM {T_ACC} WHERE id = $1::uuid AND org_id = $2::uuid AND system_to IS NULL "
                            "AND valid_to IS NULL", _uid(plan_account_id, "plan account"), str(org_id))
    if r is None:
        raise Section351NotFound("plan account not found")
    return dict(r)


async def create_plan(pool, *, org_id, actor_id, name: str, target_etf: str, sponsor_profile_id, valuation_date: date,
                      seed_capital: Decimal = ZERO, seed_note: str | None = None, sponsor_trust: str | None = None,
                      etf_custodian_account: str | None = None, today: date | None = None) -> dict:
    today = today or date.today()
    if not (name or "").strip() or not (target_etf or "").strip():
        raise Section351Error("a plan has a name and a target ETF (recorded as text)")
    if not isinstance(valuation_date, date):
        raise Section351Error("the valuation date is a date")
    if not isinstance(seed_capital, Decimal) or seed_capital < 0:
        raise Section351Error("outside seed capital is a decimal of zero or more")
    async with pool.acquire() as conn:
        actor = await _human(conn, actor_id, org_id)
        prof = await profile_version(conn, org_id, sponsor_profile_id)
        if prof["valid_to"] is not None:
            raise Section351Conflict(f"sponsor profile version {prof['version_no']} has been replaced — choose the "
                                     "current version")
        if prof["status"] != "active":
            raise Section351Conflict("the sponsor profile is retired")
        async with conn.transaction():
            row = await conn.fetchrow(
                f"""INSERT INTO {T_PLAN} (org_id, name, target_etf, sponsor_trust, sponsor_profile_id,
                        sponsor_profile_version, valuation_date, seed_capital, seed_note, etf_custodian_account,
                        maker_id) VALUES ($1::uuid, $2, $3, $4, $5::uuid, $6, $7, $8, $9, $10, $11::uuid) RETURNING *""",
                str(org_id), name.strip()[:300], target_etf.strip()[:300], (sponsor_trust or "").strip() or None,
                str(prof["id"]), prof["version_no"], valuation_date, seed_capital, (seed_note or "").strip() or None,
                (etf_custodian_account or "").strip() or None, str(actor["id"]))
            for i, (key, _label, days) in enumerate(STEPS, start=1):
                await conn.execute(
                    f"INSERT INTO {T_STEP} (org_id, plan_id, step_key, seq, due_on, owner_id) VALUES ($1::uuid, $2, $3, "
                    "$4, $5, $6::uuid)", str(org_id), row["id"], key, i, today + timedelta(days=days), str(actor["id"]))
            await _audit(conn, org_id, actor["id"], "plan_created", SUBJECT_PLAN, row["id"],
                         {"name": name, "sponsor_profile_version": prof["version_no"]})
    await _open_step_tasks(pool, org_id, row["id"], actor["id"])
    async with pool.acquire() as conn:
        return await detail(conn, org_id, row["id"])


async def _draft_only(conn, org_id, plan_id) -> dict:
    p = await load_plan(conn, org_id, plan_id)
    if p["status"] != "draft":
        raise Section351Conflict(f"the plan is {PLAN_STATUS_LABELS[p['status']].lower()} — reopen it as a draft to "
                                 "change it")
    return p


async def update_plan(pool, *, org_id, actor_id, plan_id, fields: dict) -> dict:
    allowed = {"name", "target_etf", "sponsor_trust", "valuation_date", "seed_capital", "seed_note",
               "etf_custodian_account"}
    bad = set(fields) - allowed
    if bad:
        raise Section351Error(f"unknown field(s) {sorted(bad)}")
    if "seed_capital" in fields and (not isinstance(fields["seed_capital"], Decimal) or fields["seed_capital"] < 0):
        raise Section351Error("outside seed capital is a decimal of zero or more")
    async with pool.acquire() as conn:
        actor = await _human(conn, actor_id, org_id)
        p = await _draft_only(conn, org_id, plan_id)
        async with conn.transaction():
            await _update(conn, T_PLAN, p["id"], fields)
            if "valuation_date" in fields:     # every test was at the old date
                for a in await plan_accounts(conn, p["id"]):
                    await _update(conn, T_ACC, a["id"], {"test_verdict": "not_run", "test_result": "{}",
                                                         "contributed_value": None})
            await _audit(conn, org_id, actor["id"], "plan_updated", SUBJECT_PLAN, p["id"], {"fields": sorted(fields)})
    await retest(pool, org_id=org_id, actor_id=actor_id, plan_id=plan_id)
    async with pool.acquire() as conn:
        return await detail(conn, org_id, plan_id)


async def add_account(pool, *, org_id, actor_id, plan_id, account_id, lot_ids: list | None = None,
                      today: date | None = None) -> dict:
    from services import account_models as AM

    today = today or date.today()
    async with pool.acquire() as conn:
        actor = await _human(conn, actor_id, org_id)
        p = await _draft_only(conn, org_id, plan_id)
        acct = await AM.load_account(conn, org_id, account_id)
        sc = await account_screen(conn, org_id, acct, today)
        if not sc["eligible"]:
            raise Section351Conflict(f"{acct['account_number_masked']} is not eligible: "
                                     + "; ".join(r["reason"] for r in sc["reasons"]))
        prof = await profile_version(conn, org_id, p["sponsor_profile_id"])
        ot, how = await owner_type_of(conn, org_id, acct)
        dec = owner_decision(ot, prof)
        if dec["decision"] == "refused":
            raise Section351Conflict(f"{acct['account_number_masked']}: {dec['reason']} ({how})")
        lids = [_uid(x, "tax lot") for x in lot_ids] if lot_ids else None
        test = await contribution(conn, org_id, acct, valuation_date=p["valuation_date"], profile=prof,
                                  lot_ids=lids, today=today)
        async with conn.transaction():
            try:
                row = await conn.fetchrow(
                    f"""INSERT INTO {T_ACC} (org_id, plan_id, account_id, owner_type, eligibility, lot_ids,
                            test_result, test_verdict, contributed_value, added_by)
                        VALUES ($1::uuid, $2, $3::uuid, $4, $5::jsonb, $6::uuid[], $7::jsonb, $8, $9, $10::uuid)
                        RETURNING *""",
                    str(org_id), p["id"], str(acct["id"]), ot, C.dumps({**dec, "how": how, "sponsor_review": None}),
                    lids, C.dumps(test), test["verdict"], Decimal(test["contributed_value_exact"]), str(actor["id"]))
            except Exception as exc:  # noqa: BLE001
                import asyncpg

                if isinstance(exc, asyncpg.UniqueViolationError):
                    raise Section351Conflict("the account is already in this plan") from exc
                raise
            await _audit(conn, org_id, actor["id"], "account_added", SUBJECT_PLAN, p["id"],
                         {"account_id": str(acct["id"]), "verdict": test["verdict"], "owner_type": ot})
        return account_out(dict(row))


async def retest(pool, *, org_id, actor_id, plan_id, today: date | None = None) -> list[dict]:
    """Re-run every account's test at the plan's valuation date (the stored result is replaced, system axis)."""
    from services import account_models as AM

    async with pool.acquire() as conn:
        await _human(conn, actor_id, org_id)
        p = await _draft_only(conn, org_id, plan_id)       # approved transfers read the stored test lots
        prof = await profile_version(conn, org_id, p["sponsor_profile_id"])
        out = []
        for a in await plan_accounts(conn, p["id"]):
            if a["status"] != "active":
                continue
            acct = await AM.load_account(conn, org_id, a["account_id"])
            test = await contribution(conn, org_id, acct, valuation_date=p["valuation_date"], profile=prof,
                                      lot_ids=[str(x) for x in a["lot_ids"]] if a["lot_ids"] else None, today=today)
            async with conn.transaction():
                row = await _update(conn, T_ACC, a["id"], {"test_result": C.dumps(test), "test_verdict": test["verdict"],
                                                           "contributed_value": Decimal(test["contributed_value_exact"])})
            out.append(account_out(row))
        return out


async def _account_change(pool, org_id, actor_id, plan_account_id, fields_fn, action: str, *, draft: bool = True):
    async with pool.acquire() as conn:
        actor = await _human(conn, actor_id, org_id)
        a = await load_plan_account(conn, org_id, plan_account_id)
        p = await (_draft_only(conn, org_id, a["plan_id"]) if draft else load_plan(conn, org_id, a["plan_id"]))
        fields = fields_fn(actor, a)
        async with conn.transaction():
            row = await _update(conn, T_ACC, a["id"], fields)
            await _audit(conn, org_id, actor["id"], action, SUBJECT_PLAN, p["id"],
                         {"plan_account_id": str(a["id"]), "fields": sorted(fields)})
        return account_out(row)


async def remove_account(pool, *, org_id, actor_id, plan_account_id, reason: str) -> dict:
    if not (reason or "").strip():
        raise Section351Error("say why the account leaves the plan")
    return await _account_change(pool, org_id, actor_id, plan_account_id,
                                 lambda a, x: {"status": "removed", "removed_reason": reason.strip()}, "account_removed")


async def record_us_person(pool, *, org_id, actor_id, plan_account_id, us_person: str, source: str,
                           as_of: date) -> dict:
    if us_person not in ("yes", "no") or not (source or "").strip() or not isinstance(as_of, date):
        raise Section351Error("record yes or no, with its source and as-of date (the adviser's confirmation)")
    return await _account_change(pool, org_id, actor_id, plan_account_id,
                                 lambda a, x: {"us_person": us_person, "us_person_source": source.strip()[:500],
                                               "us_person_as_of": as_of}, "us_person_recorded")


async def record_sponsor_review(pool, *, org_id, actor_id, plan_account_id, decision: str, note: str,
                                reviewed_on: date) -> dict:
    """A 'needs sponsor review' owner (partnership / LLC): the sponsor's decision, recorded with its date."""
    if decision not in ("accepted", "declined") or not (note or "").strip() or not isinstance(reviewed_on, date):
        raise Section351Error("record the sponsor's decision (accepted / declined), a note and the date")

    def fields(actor, a):
        el = C.loads(a["eligibility"]) or {}
        if el.get("decision") != "needs_sponsor_review":
            raise Section351Conflict("this account does not need the sponsor's review")
        el["sponsor_review"] = {"decision": decision, "note": note.strip()[:1000], "on": reviewed_on.isoformat(),
                                "recorded_by": str(actor["id"])}
        return {"eligibility": C.dumps(el)}
    return await _account_change(pool, org_id, actor_id, plan_account_id, fields, "sponsor_review_recorded")


async def record_consent(pool, *, org_id, actor_id, plan_account_id, document_id, signed_on: date,
                         signer: str) -> dict:
    """The member's WRITTEN consent: a document in the vault (Chancery), its signing date and the signer."""
    if not isinstance(signed_on, date) or signed_on > date.today() or not (signer or "").strip():
        raise Section351Error("a consent has its document, a signing date (not in the future) and the signer")
    did = _uid(document_id, "document")
    async with pool.acquire() as conn:
        if not await conn.fetchval("SELECT 1 FROM public.documents WHERE id = $1::uuid AND org_id = $2::uuid",
                                   did, str(org_id)):
            raise Section351NotFound("document not found")
    return await _account_change(pool, org_id, actor_id, plan_account_id,
                                 lambda a, x: {"consent_document_id": uuid.UUID(did), "consent_signed_on": signed_on,
                                               "consent_signer": signer.strip()[:300],
                                               "consent_recorded_by": a["id"]}, "consent_recorded")


async def record_tax_adviser(pool, *, org_id, actor_id, plan_account_id, adviser_name: str, confirmed_on: date,
                             note: str | None = None) -> dict:
    """The member's TAX ADVISER confirmed — recorded by a person (name, date), never inferred."""
    if not (adviser_name or "").strip() or not isinstance(confirmed_on, date) or confirmed_on > date.today():
        raise Section351Error("record the tax adviser's name and the date they confirmed (not in the future)")
    return await _account_change(pool, org_id, actor_id, plan_account_id,
                                 lambda a, x: {"tax_adviser_name": adviser_name.strip()[:300],
                                               "tax_adviser_confirmed_on": confirmed_on,
                                               "tax_adviser_note": (note or "").strip() or None,
                                               "tax_adviser_recorded_by": a["id"]}, "tax_adviser_recorded")


async def set_cash_to_member(pool, *, org_id, actor_id, plan_account_id, amount: Decimal) -> dict:
    if not isinstance(amount, Decimal) or amount < 0:
        raise Section351Error("the cash to the member is a decimal of zero or more (it is boot — taxable)")
    return await _account_change(pool, org_id, actor_id, plan_account_id,
                                 lambda a, x: {"cash_to_member": amount}, "boot_set")


def _subject(a: dict, household_id=None):
    from services import disclosures as DL

    return DL.Subject(subject_type=SUBJECT_PLAN, subject_id=str(a["id"]), account_id=str(a["account_id"]),
                      household_id=_s(household_id))


async def disclosure_state(conn, org_id, a: dict) -> dict:
    """The section351.exit version in force for the account (library ONLY — no text in code) and whether it was
    acknowledged for this plan account at exactly that version (id + hash)."""
    from services import disclosures as DL

    hh = await conn.fetchval("SELECT household_id FROM public.accounts WHERE id = $1::uuid AND system_to IS NULL "
                             "AND valid_to IS NULL", str(a["account_id"]))
    res = await DL.resolve_safe(conn, DISCLOSURE_KEY, org_id=org_id, account_id=str(a["account_id"]))
    if res["source"] != "library":
        return {"resolved": DL.public(res), "in_force": False, "acknowledged": None, "current": False,
                "reason": "no section351.exit disclosure version is published — publish one in the disclosure "
                          "library first (it is never text in code)"}
    last = await conn.fetchrow(
        f"SELECT * FROM {DL.T_ACK} WHERE org_id = $1::uuid AND disclosure_id = $2::uuid AND subject_type = $3 "
        "AND subject_id = $4::uuid ORDER BY acknowledged_at DESC LIMIT 1", str(org_id), res["disclosure_id"],
        SUBJECT_PLAN, str(a["id"]))
    current = bool(last and str(last["version_id"]) == res["version_id"]
                   and last["content_sha256"] == res["content_sha256"])
    reason = None
    if not current:
        reason = (f"a newer version of '{res['title']}' (version {res['version_no']}) was published since the "
                  "acknowledgement — acknowledge it again") if last else \
            f"acknowledge '{res['title']}' (version {res['version_no']}) for this account"
    return {"resolved": DL.public(res), "in_force": True, "household_id": _s(hh),
            "acknowledged": None if last is None else {"version_id": str(last["version_id"]),
                                                       "acknowledged_by": str(last["user_id"]),
                                                       "acknowledged_at": C.iso(last["acknowledged_at"])},
            "current": current, "reason": reason}


async def acknowledge_disclosure(pool, *, org_id, actor_id, plan_account_id, version_id) -> dict:
    """The adviser acknowledges the in-force version FOR THIS ACCOUNT (the version the screen showed — a stale one
    is refused)."""
    from services import disclosures as DL

    async with pool.acquire() as conn:
        actor = await _human(conn, actor_id, org_id)
        a = await load_plan_account(conn, org_id, plan_account_id)
        await load_plan(conn, org_id, a["plan_id"])
        res = await DL.resolve(conn, DISCLOSURE_KEY, org_id=org_id, account_id=str(a["account_id"]))
        if res["source"] != "library":
            raise Section351Conflict("no section351.exit disclosure version is published — nothing to acknowledge")
        if str(version_id) != res["version_id"]:
            raise Section351Conflict(f"version {version_id} is not the version in force (version {res['version_no']})"
                                     " — read the current version and acknowledge it")
        hh = await conn.fetchval("SELECT household_id FROM public.accounts WHERE id = $1::uuid", str(a["account_id"]))
        async with conn.transaction():
            await DL.acknowledge(conn, org_id=org_id, user_id=actor["id"], resolved=res, subject=_subject(a, hh),
                                 screen="replication.section351.plan")
        return await disclosure_state(conn, org_id, a)


async def readiness(conn, org_id, p: dict) -> dict:
    """Every gate, every account (read-only; the stored test results — :func:`mark_ready` re-runs them first)."""
    prof = await profile_version(conn, org_id, p["sponsor_profile_id"])
    accts = await plan_accounts(conn, p["id"])
    active = [a for a in accts if a["status"] in ("active", "transferred")]
    gates, per = [], []
    for a in active:
        el = C.loads(a["eligibility"]) or {}
        tr = C.loads(a["test_result"]) or {}
        g = []
        rev = el.get("sponsor_review") or {}
        if el.get("decision") == "eligible":
            g.append({"gate": "owner", "ok": True, "detail": el.get("reason")})
        elif el.get("decision") == "needs_sponsor_review":
            g.append({"gate": "owner", "ok": rev.get("decision") == "accepted",
                      "detail": "the sponsor accepted it" if rev.get("decision") == "accepted"
                      else ("the sponsor declined it" if rev else "needs the sponsor's review — record its decision")})
        else:
            g.append({"gate": "owner", "ok": False, "detail": el.get("reason") or "not eligible"})
        if prof["us_persons_only"]:
            g.append({"gate": "us_person", "ok": a["us_person"] == "yes",
                      "detail": {"yes": "U.S. person — confirmed", "no": "not a U.S. person — not eligible"}.get(
                          a["us_person"], "U.S. person status not confirmed by the adviser")})
        g.append({"gate": "diversification", "ok": a["test_verdict"] == "pass",
                  "detail": R.VERDICT_LABELS[a["test_verdict"]] + ("" if a["test_verdict"] == "pass" else
                                                                    ": " + "; ".join(tr.get("reasons", []) +
                                                                                     tr.get("blocked", [])))})
        g.append({"gate": "consent", "ok": a["consent_document_id"] is not None,
                  "detail": "written consent recorded" if a["consent_document_id"] else "the member's written consent "
                                                                                       "is missing"})
        g.append({"gate": "tax_adviser", "ok": a["tax_adviser_confirmed_on"] is not None,
                  "detail": "the member's tax adviser confirmed" if a["tax_adviser_confirmed_on"] else
                  "the member's tax adviser's confirmation is missing (recorded, never inferred)"})
        ds = await disclosure_state(conn, org_id, a)
        g.append({"gate": "disclosure", "ok": ds["current"], "detail": "acknowledged at the version in force"
                  if ds["current"] else ds["reason"], "version_id": (ds["resolved"] or {}).get("version_id")})
        per.append({"plan_account_id": str(a["id"]), "account_id": str(a["account_id"]), "gates": g,
                    "ok": all(x["ok"] for x in g)})
    ctrl = R.control_test([{"contributed_value": a["contributed_value"] or 0, "cash_to_member": a["cash_to_member"]}
                           for a in active], seed_capital=Decimal(str(p["seed_capital"])))
    gates.append({"gate": "accounts", "ok": bool(active), "detail": f"{len(active)} contributing account(s)"})
    gates.append({"gate": "control", "ok": ctrl["verdict"] == "pass",
                  "detail": f"contributors {ctrl['contributors_share_pct']} of the ETF (80 % needed)"})
    ok = all(x["ok"] for x in gates) and all(x["ok"] for x in per)
    return {"ok": ok, "plan_gates": gates, "accounts": per, "control": ctrl, "label": R.LABEL}


def _failures(r: dict) -> list[dict]:
    out = [g for g in r["plan_gates"] if not g["ok"]]
    for a in r["accounts"]:
        out += [{**g, "account_id": a["account_id"]} for g in a["gates"] if not g["ok"]]
    return out


async def mark_ready(pool, *, org_id, actor_id, plan_id) -> dict:
    """draft → ready: every account re-tested at the valuation date, then every gate; refused with the open gates."""
    await retest(pool, org_id=org_id, actor_id=actor_id, plan_id=plan_id)
    async with pool.acquire() as conn:
        actor = await _human(conn, actor_id, org_id)
        p = await _draft_only(conn, org_id, plan_id)
        r = await readiness(conn, org_id, p)
        if not r["ok"]:
            fails = _failures(r)
            raise NotReady("the plan is not ready: " + "; ".join(f"{g['gate']}: {g['detail']}" for g in fails), fails)
        async with conn.transaction():
            row = await _update(conn, T_PLAN, p["id"], {"status": "ready", "ready_by": actor["id"], "ready_at": _now(),
                                                        "readiness": C.dumps(r),
                                                        "control_result": C.dumps(r["control"])})
            await _audit(conn, org_id, actor["id"], "plan_ready", SUBJECT_PLAN, p["id"],
                         {"control": r["control"]["contributors_share_pct"]})
        return plan_out(row)


async def reopen(pool, *, org_id, actor_id, plan_id) -> dict:
    async with pool.acquire() as conn:
        actor = await _human(conn, actor_id, org_id)
        p = await load_plan(conn, org_id, plan_id)
        if p["status"] != "ready":
            raise Section351Conflict("only a ready plan is reopened as a draft")
        async with conn.transaction():
            row = await _update(conn, T_PLAN, p["id"], {"status": "draft"})
            await _audit(conn, org_id, actor["id"], "plan_reopened", SUBJECT_PLAN, p["id"], {})
        return plan_out(row)


async def submit(pool, *, org_id, actor_id, plan_id) -> dict:
    async with pool.acquire() as conn:
        actor = await _human(conn, actor_id, org_id)
        p = await load_plan(conn, org_id, plan_id)
        if p["status"] != "ready":
            raise Section351Conflict("only a ready plan is submitted for approval")
        r = await readiness(conn, org_id, p)
        if not r["ok"]:
            fails = _failures(r)
            raise NotReady("the plan is no longer ready: " + "; ".join(f"{g['gate']}: {g['detail']}" for g in fails),
                           fails)
    task = await HT.open_task(pool, org_id=org_id, kind=TASK_PLAN, subject_type=SUBJECT_PLAN, subject_id=p["id"],
                              maker_id=actor["id"], eligible_permission=APPROVE,
                              title=f"Approve the Section 351 plan {p['name']} ({p['target_etf']})",
                              payload={"accounts": len(r["accounts"]),
                                       "control": r["control"]["contributors_share_pct"]},
                              action_path=f"/portfolio/replication/section351?plan={p['id']}")
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                row = await _update(conn, T_PLAN, p["id"], {"status": "pending_approval", "submitted_by": actor["id"],
                                                            "submitted_at": _now(),
                                                            "approval_task_id": uuid.UUID(task["id"])})
                await _audit(conn, org_id, actor["id"], "plan_submitted", SUBJECT_PLAN, p["id"], {"task_id": task["id"]})
    except Exception:
        await C.withdraw_task(pool, task["id"], "the plan could not be submitted")
        raise
    return {**plan_out(row), "task": task}


async def decide(pool, *, org_id, actor_id, plan_id, outcome: str, note: str | None = None) -> dict:
    if outcome not in ("approve", "reject"):
        raise Section351Error("outcome is approve or reject")
    if outcome == "reject" and not (note or "").strip():
        raise Section351Error("a rejection needs a note saying why")
    async with pool.acquire() as conn:
        actor = await _human(conn, actor_id, org_id)
        p = await load_plan(conn, org_id, plan_id)
    if p["status"] != "pending_approval":
        raise Section351Conflict(f"the plan is {PLAN_STATUS_LABELS[p['status']].lower()}, not awaiting approval")
    if str(actor["id"]) in (str(p["maker_id"]), _s(p["submitted_by"])):
        raise MakerCheckerViolation(f"maker-checker: you made or submitted this plan, so a different holder of "
                                    f"{APPROVE} must {outcome} it")
    try:
        await C.complete(pool, org_id=org_id, task_id=p["approval_task_id"], actor_id=actor["id"], outcome=outcome,
                         note=note)
    except C.MakerCheckerViolation as exc:
        raise MakerCheckerViolation(str(exc)) from exc
    except C.Rep10Conflict as exc:
        raise Section351Conflict(str(exc)) from exc
    async with pool.acquire() as conn:
        return await detail(conn, org_id, p["id"])


async def _precheck(pool, task, outcome, actor_id):
    """Before the engine completes an APPROVAL: every gate again (a newly published disclosure, a changed holding)."""
    if outcome != "approve":
        return
    async with pool.acquire() as conn:
        p = await load_plan(conn, task["org_id"], task["subject_id"])
        r = await readiness(conn, task["org_id"], p)
    if not r["ok"]:
        raise HT.HumanTaskConflict("the plan is no longer ready: " + "; ".join(
            f"{g['gate']}: {g['detail']}" for g in _failures(r)))


async def _apply(pool, task: dict, outcome: str, actor_id, note) -> dict | None:
    org_id = task["org_id"]
    async with pool.acquire() as conn:
        async with conn.transaction():
            p = await conn.fetchrow(f"SELECT * FROM {T_PLAN} WHERE id = $1::uuid AND org_id = $2::uuid AND system_to "
                                    "IS NULL FOR UPDATE", task["subject_id"], str(org_id))
            if p is None or p["status"] != "pending_approval":
                return None
            checker = uuid.UUID(str(actor_id))
            if checker in (p["maker_id"], p["submitted_by"]):
                raise MakerCheckerViolation("maker-checker: the maker of a plan cannot approve it")
            now = _now()
            if outcome != "approve":
                row = await _update(conn, T_PLAN, p["id"], {"status": "draft", "decision_note": note,
                                                            "approval_task_id": None})
                await _audit(conn, org_id, checker, "plan_rejected", SUBJECT_PLAN, p["id"], {"note": note})
                return plan_out(row)
            row = await _update(conn, T_PLAN, p["id"], {"status": "approved", "approved_by": checker,
                                                        "approved_at": now, "decision_note": note})
            n = 0
            makers = sorted({p["maker_id"], p["submitted_by"]} - {None}, key=str)
            dest = p["etf_custodian_account"] or f"the ETF's custodian account for {p['target_etf']} (to be confirmed)"
            for a in await plan_accounts(conn, p["id"]):
                if a["status"] != "active":
                    continue
                for lot in (C.loads(a["test_result"]) or {}).get("lots", []):
                    await conn.execute(
                        f"""INSERT INTO {T_XFER} (org_id, plan_id, plan_account_id, account_id, tax_lot_id,
                                global_security_id, quantity, basis, acquired_date, fmv, destination, maker_ids,
                                approved_by, approved_at)
                            VALUES ($1::uuid, $2, $3, $4, $5::uuid, $6::uuid, $7, $8, $9, $10, $11, $12::uuid[],
                                    $13, $14)""",
                        str(org_id), p["id"], a["id"], a["account_id"], lot["tax_lot_id"], lot["security_id"],
                        Decimal(lot["quantity"]), Decimal(lot["basis"]), date.fromisoformat(lot["acquired_date"]),
                        Decimal(lot["fmv"]), dest, makers, checker, now)
                    n += 1
            await _audit(conn, org_id, checker, "plan_approved", SUBJECT_PLAN, p["id"],
                         {"task_id": task["id"], "transfer_instructions": n})
    return plan_out(row)


async def _on_complete(pool, task, outcome, actor_id, note):
    return await _apply(pool, task, outcome, actor_id, note)


HT.register_kind(TASK_PLAN, label="Section 351 plan approval", outcomes=("approve", "reject"), maker_checker=True,
                 on_complete=_on_complete)
HT.register_precheck(TASK_PLAN, _precheck)


async def cancel(pool, *, org_id, actor_id, plan_id, reason: str) -> dict:
    if not (reason or "").strip():
        raise Section351Error("say why the plan is cancelled")
    async with pool.acquire() as conn:
        actor = await _human(conn, actor_id, org_id)
        p = await load_plan(conn, org_id, plan_id)
        if p["status"] in ("completed", "cancelled", "transferring"):
            raise Section351Conflict(f"a {PLAN_STATUS_LABELS[p['status']].lower()} plan cannot be cancelled")
        async with conn.transaction():
            row = await _update(conn, T_PLAN, p["id"], {"status": "cancelled", "cancelled_by": actor["id"],
                                                        "cancelled_at": _now(), "cancel_reason": reason.strip()})
            for x in await conn.fetch(f"SELECT id FROM {T_XFER} WHERE plan_id = $1 AND status = 'approved' "
                                      "AND system_to IS NULL", p["id"]):
                await _update(conn, T_XFER, x["id"], {"status": "cancelled", "cancelled_by": actor["id"],
                                                      "cancelled_at": _now(), "cancel_reason": reason.strip()})
            await _audit(conn, org_id, actor["id"], "plan_cancelled", SUBJECT_PLAN, p["id"], {"reason": reason})
    if p["status"] == "pending_approval" and p["approval_task_id"]:
        await C.withdraw_task(pool, p["approval_task_id"], "the plan was cancelled")
    return plan_out(row)


# ═══ Task 6 — the basis record (CSV + PDF in the vault, versioned) ════════════════════════════════════════════════
BASIS_COLUMNS = ("contributor_account", "owner_type", "tax_lot_id", "security_id", "security", "quantity",
                 "basis", "acquired_date", "holding_period_at_valuation", "fmv_at_valuation", "boot_allocated",
                 "gain_recognized", "etf_basis_carryover", "etf_acquired_date_tacked", "etf_shares")


async def basis_lines(conn, org_id, p: dict) -> dict:
    accts = [a for a in await plan_accounts(conn, p["id"]) if a["status"] in ("active", "transferred")]
    contributors, lines = [], []
    total_fmv = total_basis = total_etf_basis = ZERO
    for a in accts:
        tr = C.loads(a["test_result"]) or {}
        lots = tr.get("lots") or []
        if not lots:
            raise Section351Conflict(f"account {tr.get('account') or a['account_id']}: no tested lots — re-test first")
        co = R.carryover(lots, etf_shares=a["etf_shares"], cash_to_member=Decimal(str(a["cash_to_member"])),
                         on=p["valuation_date"])
        masked = tr.get("account") or str(a["account_id"])
        for ln in co["lines"]:
            lines.append({"contributor_account": masked, "owner_type": a["owner_type"], **ln})
        t = co["totals"]
        total_fmv += Decimal(t["fmv"])
        total_basis += Decimal(t["basis"])
        total_etf_basis += Decimal(t["etf_basis"])
        contributors.append({"plan_account_id": str(a["id"]), "account": masked, "owner_type": a["owner_type"],
                             **t, "contributed_value_on_plan": str(R.money(Decimal(str(a["contributed_value"] or 0))))})
    plan_contributed = R.money(sum((Decimal(str(a["contributed_value"] or 0)) for a in accts), ZERO))
    reconciles = R.money(total_fmv) == plan_contributed
    return {"lines": lines, "contributors": contributors,
            "totals": {"contributors": len(contributors), "lots": len(lines), "fmv": str(R.money(total_fmv)),
                       "basis": str(R.money(total_basis)), "etf_basis": str(R.money(total_etf_basis)),
                       "plan_contributed_value": str(plan_contributed), "reconciles_to_plan": reconciles},
            "label": R.LABEL}


def basis_csv(p: dict, b: dict) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["# Section 351 basis record", p["name"], p["target_etf"], f"valuation date {p['valuation_date']}",
                R.LABEL])
    w.writerow(BASIS_COLUMNS)
    for ln in b["lines"]:
        w.writerow([ln["contributor_account"], ln["owner_type"], ln["tax_lot_id"], ln["security_id"], ln["label"],
                    ln["quantity"], ln["basis"], ln["acquired_date"], ln["holding_period_on_transfer"] or "",
                    ln["fmv"], ln["boot_allocated"], ln["gain_recognized"], ln["etf_basis"], ln["etf_acquired_date"],
                    ln["etf_shares"] or "per the sponsor at launch"])
    t = b["totals"]
    w.writerow(["TOTAL", "", "", "", "", "", t["basis"], "", "", t["fmv"], "", "", t["etf_basis"], "", ""])
    return buf.getvalue().encode("utf-8")


def basis_pdf(p: dict, b: dict, *, org_name: str, version_no: int) -> bytes:
    from reportlab.lib.utils import simpleSplit
    from reportlab.pdfgen import canvas

    from services.deal_room_pdf import MARGIN, MUTED, NAVY, PAGE_H, PAGE_W, _safe

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(PAGE_W, PAGE_H), pageCompression=1, invariant=1)
    c.setTitle(_safe(f"Section 351 basis record {p['id']} v{version_no}"))
    width = PAGE_W - 2 * MARGIN
    y = PAGE_H - MARGIN

    def line(text, font="Helvetica", size=9, color=(0, 0, 0), gap=12):
        nonlocal y
        c.setFillColorRGB(*color)
        c.setFont(font, size)
        for ln in simpleSplit(_safe(text), font, size, width) or [""]:
            if y < MARGIN + 30:
                c.showPage()
                y = PAGE_H - MARGIN
                c.setFillColorRGB(*color)
                c.setFont(font, size)
            c.drawString(MARGIN, y, ln)
            y -= gap

    line(org_name, "Times-Bold", 13, NAVY, gap=18)
    line(f"Section 351 basis record — {p['name']} (version {version_no})", "Times-Roman", 14, NAVY, gap=20)
    line(f"Target ETF: {p['target_etf']}   Valuation date: {p['valuation_date']}   {R.LABEL}", color=MUTED)
    line("For the ETF's transfer agent: every contributed lot with its basis and acquisition date. The ETF shares "
         "take the lot's basis (carryover, §358 / §362) and its holding period is tacked (§1223).", gap=16)
    for ct in b["contributors"]:
        line(f"Contributor {ct['account']} ({OWNER_LABELS.get(ct['owner_type'], ct['owner_type'])}): "
             f"{ct['lots']} lot(s), value {ct['fmv']}, basis {ct['basis']}, ETF basis {ct['etf_basis']}",
             "Helvetica-Bold", 9, NAVY, gap=13)
        for ln in [x for x in b["lines"] if x["contributor_account"] == ct["account"]]:
            line(f"  {ln['label']}: {ln['quantity']} sh, lot {ln['tax_lot_id'][:8]}, basis {ln['basis']}, acquired "
                 f"{ln['acquired_date']} ({ln['holding_period_on_transfer'] or '—'}), value {ln['fmv']} → ETF basis "
                 f"{ln['etf_basis']}, acquired {ln['etf_acquired_date']} (tacked)")
    t = b["totals"]
    line(f"Totals: {t['lots']} lots, value {t['fmv']}, basis {t['basis']}, ETF basis {t['etf_basis']}; reconciles "
         f"to the plan ({t['plan_contributed_value']}): {'yes' if t['reconciles_to_plan'] else 'NO'}",
         "Helvetica-Bold", 9, gap=16)
    line("Not tax or legal advice. Every figure is an estimate — the member's tax adviser and counsel confirm; the "
         "sponsor runs its own final compliance review.", size=8, color=MUTED)
    c.showPage()
    c.save()
    return buf.getvalue()


def basis_out(r: dict) -> dict:
    return {"id": str(r["id"]), "plan_id": str(r["plan_id"]), "version_no": r["version_no"],
            "csv_document_id": str(r["csv_document_id"]), "csv_sha256": r["csv_sha256"],
            "pdf_document_id": str(r["pdf_document_id"]), "pdf_sha256": r["pdf_sha256"],
            "totals": C.loads(r["totals"]) or {}, "created_by": str(r["created_by"]),
            "created_at": C.iso(r["created_at"]), "delivered_on": C.iso(r["delivered_on"]),
            "delivered_note": r["delivered_note"], "live": r["valid_to"] is None}


async def export_basis_record(pool, *, org_id, actor_id, plan_id) -> dict:
    """A NEW VERSION of the basis record: CSV + PDF stored in R2 under the org, each a document in the vault (hash
    recorded); the earlier version is closed (kept). Refused unless the totals reconcile to the plan to the cent."""
    from starlette.concurrency import run_in_threadpool

    from services import storage

    async with pool.acquire() as conn:
        actor = await _human(conn, actor_id, org_id)
        p = await load_plan(conn, org_id, plan_id)
        if p["status"] in ("draft", "cancelled"):
            raise Section351Conflict("the basis record is exported from a ready (or later) plan")
        b = await basis_lines(conn, org_id, p)
        if not b["totals"]["reconciles_to_plan"]:
            raise Section351Conflict(f"the basis record's value {b['totals']['fmv']} does not reconcile to the plan's "
                                     f"{b['totals']['plan_contributed_value']} — re-test the accounts")
        org_name = await conn.fetchval("SELECT name FROM public.organizations WHERE id = $1::uuid", str(org_id)) or ""
        n = (await conn.fetchval(f"SELECT max(version_no) FROM {T_BASIS} WHERE plan_id = $1", p["id"]) or 0) + 1
    data_csv = basis_csv(p, b)
    data_pdf = basis_pdf(p, b, org_name=org_name, version_no=n)
    files = []
    for data, ext, ctype in ((data_csv, "csv", "text/csv"), (data_pdf, "pdf", "application/pdf")):
        sha = hashlib.sha256(data).hexdigest()
        key = f"orgs/{org_id}/section351/{p['id']}/basis-record-v{n}-{sha}.{ext}"
        await run_in_threadpool(storage.upload_bytes, key, data, ctype, storage.DEFAULT_BUCKET)
        files.append((sha, key, ctype, ext))
    async with pool.acquire() as conn:
        async with conn.transaction():
            doc_ids = []
            for sha, key, ctype, ext in files:
                doc_ids.append(await conn.fetchval(
                    "INSERT INTO public.documents (org_id, original_filename, source, mime_type, storage_key, status, "
                    "doc_family, retention_classification, created_by) VALUES ($1::uuid, $2, 'generated', $3, $4, "
                    "'stored', 'section351_basis_record', 'permanent', $5::uuid) RETURNING id", str(org_id),
                    f"section351-basis-record-{p['id']}-v{n}.{ext}", ctype, key, str(actor["id"])))
            await conn.execute(f"UPDATE {T_BASIS} SET valid_to = clock_timestamp() WHERE plan_id = $1 AND valid_to IS "
                               "NULL AND system_to IS NULL", p["id"])
            row = await conn.fetchrow(
                f"""INSERT INTO {T_BASIS} (org_id, plan_id, version_no, csv_document_id, csv_sha256, pdf_document_id,
                        pdf_sha256, totals, lines, created_by, valid_from)
                    VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, $8::jsonb, $9::jsonb, $10::uuid, clock_timestamp())
                    RETURNING *""", str(org_id), p["id"], n, doc_ids[0], files[0][0], doc_ids[1], files[1][0],
                C.dumps({**b["totals"], "contributors": b["contributors"]}), C.dumps(b["lines"]), str(actor["id"]))
            await _audit(conn, org_id, actor["id"], "basis_record_exported", SUBJECT_PLAN, p["id"],
                         {"version_no": n, "csv_sha256": files[0][0], "pdf_sha256": files[1][0]})
    return basis_out(dict(row))


async def basis_records(conn, org_id, plan_id) -> list[dict]:
    return [basis_out(dict(r)) for r in await conn.fetch(
        f"SELECT * FROM {T_BASIS} WHERE plan_id = $1::uuid AND org_id = $2::uuid AND system_to IS NULL "
        "ORDER BY version_no DESC", _uid(plan_id, "plan"), str(org_id))]


async def basis_file(conn, org_id, record_id, kind: str) -> tuple[bytes, str, str]:
    """(bytes, content type, file name) — read back through the API under RLS, hash-checked."""
    from starlette.concurrency import run_in_threadpool

    from services import storage

    if kind not in ("csv", "pdf"):
        raise Section351Error("kind is csv or pdf")
    r = await conn.fetchrow(f"SELECT * FROM {T_BASIS} WHERE id = $1::uuid AND org_id = $2::uuid AND system_to IS NULL",
                            _uid(record_id, "basis record"), str(org_id))
    if r is None:
        raise Section351NotFound("basis record not found")
    d = await conn.fetchrow("SELECT storage_key, mime_type, original_filename FROM public.documents WHERE id = $1 "
                            "AND org_id = $2::uuid", r[f"{kind}_document_id"], str(org_id))
    if d is None:
        raise Section351NotFound("document not found")
    data = await run_in_threadpool(storage.download_bytes, d["storage_key"])
    if hashlib.sha256(data).hexdigest() != r[f"{kind}_sha256"]:
        raise Section351Conflict("the stored file does not match its recorded hash — it is not served")
    return data, d["mime_type"], d["original_filename"]


async def record_delivery(pool, *, org_id, actor_id, record_id, delivered_on: date, note: str | None = None) -> dict:
    if not isinstance(delivered_on, date) or delivered_on > date.today():
        raise Section351Error("the delivery date is a date, not in the future")
    async with pool.acquire() as conn:
        actor = await _human(conn, actor_id, org_id)
        async with conn.transaction():
            r = await conn.fetchrow(f"UPDATE {T_BASIS} SET delivered_on = $3, delivered_note = $4 WHERE id = $1::uuid "
                                    "AND org_id = $2::uuid AND valid_to IS NULL AND system_to IS NULL AND delivered_on "
                                    "IS NULL RETURNING *", _uid(record_id, "basis record"), str(org_id), delivered_on,
                                    (note or "").strip() or None)
            if r is None:
                raise Section351Conflict("only the live, undelivered version records a delivery")
            await _audit(conn, org_id, actor["id"], "basis_record_delivered", SUBJECT_PLAN, r["plan_id"],
                         {"version_no": r["version_no"], "delivered_on": delivered_on.isoformat()})
    return basis_out(dict(r))


# ═══ Task 7 — the project tracker (human tasks on the workflow engine) ═══════════════════════════════════════════
def step_out(r: dict, created: date | None = None) -> dict:
    out = {"id": str(r["id"]), "step_key": r["step_key"], "label": STEP_LABELS[r["step_key"]], "seq": r["seq"],
           "owner_id": _s(r["owner_id"]), "due_on": C.iso(r["due_on"]), "status": r["status"],
           "task_id": _s(r["task_id"]), "done_by": _s(r["done_by"]), "done_at": C.iso(r["done_at"]), "note": r["note"]}
    if created and r["due_on"]:
        out["month"] = round((r["due_on"] - created).days / 30.44, 1)
    return out


async def steps_of(conn, plan_id) -> list[dict]:
    return [dict(r) for r in await conn.fetch(f"SELECT * FROM {T_STEP} WHERE plan_id = $1::uuid AND system_to IS NULL "
                                              "AND valid_to IS NULL ORDER BY seq", str(plan_id))]


async def _open_step_tasks(pool, org_id, plan_id, maker_id) -> None:
    async with pool.acquire() as conn:
        p = await load_plan(conn, org_id, plan_id)
        steps = await steps_of(conn, plan_id)
    for s in steps:
        if s["task_id"] or s["status"] != "open":
            continue
        task = await HT.open_task(pool, org_id=org_id, kind=TASK_STEP, subject_type=SUBJECT_STEP, subject_id=s["id"],
                                  maker_id=maker_id, eligible_permission=MANAGE,
                                  title=f"Section 351 — {p['name']}: {STEP_LABELS[s['step_key']]}",
                                  payload={"plan_id": str(plan_id), "step_key": s["step_key"],
                                           "due_on": C.iso(s["due_on"])},
                                  action_path=f"/portfolio/replication/section351?plan={plan_id}&view=tracker")
        async with pool.acquire() as conn:
            async with conn.transaction():
                await _update(conn, T_STEP, s["id"], {"task_id": uuid.UUID(task["id"])})


async def update_step(pool, *, org_id, actor_id, step_id, owner_id=None, due_on: date | None = None,
                      note: str | None = None) -> dict:
    async with pool.acquire() as conn:
        actor = await _human(conn, actor_id, org_id)
        s = await conn.fetchrow(f"SELECT * FROM {T_STEP} WHERE id = $1::uuid AND org_id = $2::uuid AND system_to IS NULL",
                                _uid(step_id, "step"), str(org_id))
        if s is None:
            raise Section351NotFound("step not found")
        fields = {}
        if owner_id is not None:
            oid = _uid(owner_id, "user")
            if not await conn.fetchval("SELECT 1 FROM public.users WHERE id = $1::uuid AND org_id = $2::uuid", oid,
                                       str(org_id)):
                raise Section351NotFound("user not found")
            fields["owner_id"] = uuid.UUID(oid)
        if due_on is not None:
            fields["due_on"] = due_on
        if note is not None:
            fields["note"] = note.strip()[:2000] or None
        if not fields:
            raise Section351Error("change the owner, the due date or the note")
        async with conn.transaction():
            row = await _update(conn, T_STEP, s["id"], fields)
            await _audit(conn, org_id, actor["id"], "step_updated", SUBJECT_PLAN, s["plan_id"],
                         {"step_key": s["step_key"], "fields": sorted(fields)})
        return step_out(row)


async def complete_step(pool, *, org_id, actor_id, step_id, note: str | None = None) -> dict:
    """Done through the step's human task (the engine records who and when)."""
    async with pool.acquire() as conn:
        await _human(conn, actor_id, org_id)
        s = await conn.fetchrow(f"SELECT * FROM {T_STEP} WHERE id = $1::uuid AND org_id = $2::uuid AND system_to IS NULL",
                                _uid(step_id, "step"), str(org_id))
        if s is None:
            raise Section351NotFound("step not found")
        if s["status"] != "open" or s["task_id"] is None:
            raise Section351Conflict("the step is not open")
    try:
        await HT.complete_task(pool, org_id=org_id, task_id=s["task_id"], actor_id=actor_id, outcome="done", note=note)
    except HT.HumanTaskPermissionError as exc:
        raise PermissionError(str(exc)) from exc
    except HT.HumanTaskConflict as exc:
        raise Section351Conflict(str(exc)) from exc
    async with pool.acquire() as conn:
        r = await conn.fetchrow(f"SELECT * FROM {T_STEP} WHERE id = $1 AND system_to IS NULL", s["id"])
        return step_out(dict(r))


async def _on_step(pool, task, outcome, actor_id, note):
    async with pool.acquire() as conn:
        async with conn.transaction():
            s = await conn.fetchrow(f"SELECT * FROM {T_STEP} WHERE id = $1::uuid AND system_to IS NULL FOR UPDATE",
                                    task["subject_id"])
            if s is None or s["status"] != "open":
                return None
            row = await _update(conn, T_STEP, s["id"], {"status": "done", "done_by": uuid.UUID(str(actor_id)),
                                                        "done_at": _now(), "note": note or s["note"]})
            await _audit(conn, task["org_id"], actor_id, "step_done", SUBJECT_PLAN, s["plan_id"],
                         {"step_key": s["step_key"]})
    return step_out(row)


HT.register_kind(TASK_STEP, label="Section 351 project step", outcomes=("done",), maker_checker=False,
                 on_complete=_on_step)


async def timeline(conn, org_id, plan_id) -> dict:
    p = await load_plan(conn, org_id, plan_id)
    created = p["created_at"].date()
    steps = [step_out(s, created) for s in await steps_of(conn, p["id"])]
    last = max((s["due_on"] for s in steps if s["due_on"]), default=None)
    months = None if last is None else round((date.fromisoformat(last) - created).days / 30.44, 1)
    return {"plan_id": str(p["id"]), "start": created.isoformat(), "end": last, "months": months, "steps": steps,
            "note": TIMELINE_NOTE, "within_6_to_9_months": months is not None and 6 <= months <= 9.5}


# ═══ Task 8 — in-kind transfer instructions ═════════════════════════════════════════════════════════════════════════
def transfer_out(r: dict) -> dict:
    return {"id": str(r["id"]), "plan_account_id": str(r["plan_account_id"]), "account_id": str(r["account_id"]),
            "tax_lot_id": str(r["tax_lot_id"]), "global_security_id": str(r["global_security_id"]),
            "quantity": str(r["quantity"]), "basis": str(r["basis"]), "acquired_date": C.iso(r["acquired_date"]),
            "fmv": str(r["fmv"]), "destination": r["destination"], "kind": r["kind"],
            "kind_label": "In-kind transfer out", "status": r["status"],
            "status_label": TRANSFER_STATUS_LABELS[r["status"]], "routed_via": r["routed_via"],
            "routing_reason": r["routing_reason"], "approved_by": str(r["approved_by"]),
            "sent_at": C.iso(r["sent_at"]), "sent_channel": r["sent_channel"], "received_on": C.iso(r["received_on"]),
            "closed_lot_id": _s(r["closed_lot_id"]), "etf_lot_id": _s(r["etf_lot_id"]),
            "etf_shares": None if r["etf_shares"] is None else str(r["etf_shares"]),
            "realized_gain": None if r["realized_gain"] is None else str(r["realized_gain"])}


async def transfers_of(conn, org_id, plan_id, *, account_id=None) -> list[dict]:
    return [dict(r) for r in await conn.fetch(
        f"SELECT * FROM {T_XFER} WHERE plan_id = $1::uuid AND org_id = $2::uuid AND system_to IS NULL "
        "AND ($3::uuid IS NULL OR account_id = $3::uuid) ORDER BY account_id, created_at, id",
        _uid(plan_id, "plan"), str(org_id), _s(account_id))]


async def adapter_refusal(conn, account: dict) -> str:
    """The adapter route: refused unless the custodian DECLARES in_kind_transfers ('unknown' is not declared) — and
    even then no in-kind transfer client exists in this build. The manual route is always offered."""
    from services.custodian_capabilities import available, capability
    from services.custody.registry import trade_adapter_for, trade_custodian_key

    cls = trade_adapter_for(account.get("custodian_code"))
    code = (getattr(cls, "capability_custodian", None) or trade_custodian_key(account.get("custodian_code"))
            or "manual") if cls else trade_custodian_key(account.get("custodian_code")) or "manual"
    cap = await capability(conn, code, "in_kind_transfers")
    if not available(cap):
        return (f"the {code} adapter refuses: {code} does not declare in_kind_transfers (it is '{cap.value}' — unknown "
                "is not declared). The manual route is always offered: print the instruction sheet and send it.")
    return (f"{code} declares in_kind_transfers, but no in-kind transfer client exists in this build — use the "
            "manual route (the instruction sheet).")


async def route(pool, *, org_id, actor_id, plan_id, account_id, route_kind: str, channel: str | None = None) -> dict:
    """``route_kind`` 'adapter' → refused (see :func:`adapter_refusal`); 'manual' → the account's approved transfers
    are marked SENT by this person, with how they were sent (the printed instruction sheet)."""
    from services import account_models as AM

    if route_kind not in ("adapter", "manual"):
        raise Section351Error("route is adapter or manual")
    async with pool.acquire() as conn:
        actor = await _human(conn, actor_id, org_id)
        p = await load_plan(conn, org_id, plan_id)
        if p["status"] not in ("approved", "transferring"):
            raise Section351Conflict("only an approved plan's transfers are routed")
        acct = await AM.load_account(conn, org_id, account_id)
        xs = [x for x in await transfers_of(conn, org_id, p["id"], account_id=acct["id"]) if x["status"] == "approved"]
        if not xs:
            raise Section351Conflict("no approved transfer instructions for this account")
        if route_kind == "adapter":
            raise InKindTransferNotDeclared(await adapter_refusal(conn, acct))
        if not (channel or "").strip():
            raise Section351Error("say how the instruction sheet was sent (e.g. signed letter to the custodian)")
        async with conn.transaction():
            now = _now()
            for x in xs:
                await _update(conn, T_XFER, x["id"], {
                    "status": "sent", "routed_via": "manual", "sent_by": actor["id"], "sent_at": now,
                    "sent_channel": channel.strip()[:300],
                    "routing_reason": "manual route: the printed instruction sheet, carried out by the custodian"})
            if p["status"] == "approved":
                await _update(conn, T_PLAN, p["id"], {"status": "transferring"})
            await _audit(conn, org_id, actor["id"], "transfers_sent", SUBJECT_PLAN, p["id"],
                         {"account_id": str(acct["id"]), "instructions": len(xs), "channel": channel})
        return {"sent": len(xs), "transfers": [transfer_out(x) for x in
                                                await transfers_of(conn, org_id, p["id"], account_id=acct["id"])]}


def instruction_sheet(p: dict, accounts: list[dict], *, org_name: str) -> bytes:
    """The manual instruction sheet: one page per account, every lot to deliver in kind (free of payment) to the
    ETF's custodian account. On demand, never stored."""
    from reportlab.lib.utils import simpleSplit
    from reportlab.pdfgen import canvas

    from services.deal_room_pdf import MARGIN, MUTED, NAVY, PAGE_H, PAGE_W, _safe

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(PAGE_W, PAGE_H), pageCompression=1, invariant=1)
    c.setTitle(_safe(f"In-kind transfer instructions {p['id']}"))
    width = PAGE_W - 2 * MARGIN
    for a in accounts:
        y = PAGE_H - MARGIN

        def line(text, font="Helvetica", size=10, color=(0, 0, 0), gap=13):
            nonlocal y
            c.setFillColorRGB(*color)
            c.setFont(font, size)
            for ln in simpleSplit(_safe(text), font, size, width) or [""]:
                if y < MARGIN + 40:
                    c.showPage()
                    y = PAGE_H - MARGIN
                    c.setFillColorRGB(*color)
                    c.setFont(font, size)
                c.drawString(MARGIN, y, ln)
                y -= gap

        line(org_name, "Times-Bold", 13, NAVY, gap=18)
        line("Letter of instruction — in-kind transfer out (Section 351 contribution)", "Times-Roman", 15, NAVY, gap=22)
        line(f"Account: {a['account']}   Custodian: {a.get('custodian_code') or '—'}", color=MUTED)
        line(f"Deliver to: {a['destination']}   ETF: {p['target_etf']}", color=MUTED, gap=18)
        line("Please deliver the securities and lots below IN KIND and FREE OF PAYMENT. Do not sell them. Each lot's "
             "cost basis and acquisition date carry to the receiving account.", gap=16)
        for x in a["lots"]:
            line(f"{x['label']}: {x['quantity']} shares — lot {x['tax_lot_id']} acquired {x['acquired_date']}, basis "
                 f"{x['basis']}")
        line("No trade and no sale. Generated from the approved plan on request; not itself a record.", size=8,
             color=MUTED, gap=20)
        line("Account holder signature ______________________________     Date __________")
        c.showPage()
    c.save()
    return buf.getvalue()


async def sheet(conn, org_id, plan_id) -> bytes:
    p = await load_plan(conn, org_id, plan_id)
    if p["status"] not in ("approved", "transferring", "completed"):
        raise Section351Conflict("the instruction sheet is printed from an approved plan")
    xs = [x for x in await transfers_of(conn, org_id, p["id"]) if x["status"] != "cancelled"]
    facts = await security_facts(conn, org_id, [str(x["global_security_id"]) for x in xs])
    by: dict[str, dict] = {}
    for x in xs:
        a = by.get(str(x["account_id"]))
        if a is None:
            r = await conn.fetchrow("SELECT account_number_masked, custodian_code FROM public.accounts WHERE id = $1 "
                                    "AND system_to IS NULL AND valid_to IS NULL", x["account_id"])
            a = by[str(x["account_id"])] = {"account": r["account_number_masked"], "custodian_code": r["custodian_code"],
                                            "destination": x["destination"], "lots": []}
        a["lots"].append({"label": (facts.get(str(x["global_security_id"])) or {}).get("label")
                          or str(x["global_security_id"]), "quantity": str(x["quantity"]),
                          "tax_lot_id": str(x["tax_lot_id"]), "acquired_date": C.iso(x["acquired_date"]),
                          "basis": str(R.money(Decimal(str(x["basis"]))))})
    org_name = await conn.fetchval("SELECT name FROM public.organizations WHERE id = $1::uuid", str(org_id)) or ""
    return instruction_sheet(p, list(by.values()), org_name=org_name)


async def confirm_account(pool, *, org_id, actor_id, plan_account_id, received_on: date, etf_global_security_id,
                          etf_shares: Decimal) -> dict:
    """The custodian confirmed the delivery (a person records it): every sent lot of the account CLOSES BY TRANSFER
    (bi-temporal restatement of the lot: status closed, realized_gain_loss NULL — nothing realised), the source
    position is restated to the quantity left, and the ETF position is recorded with one lot per contributed lot
    (its share of the ETF shares by fair market value, the lot's carryover basis, the tacked acquisition date)."""
    from services import account_models as AM
    from services.held_away import _asset_for

    if not isinstance(received_on, date) or received_on > date.today():
        raise Section351Error("the received date is a date, not in the future")
    if not isinstance(etf_shares, Decimal) or etf_shares <= 0:
        raise Section351Error("the ETF shares received are a decimal above zero")
    etf = _uid(etf_global_security_id, "ETF security")
    async with pool.acquire() as conn:
        actor = await _human(conn, actor_id, org_id)
        a = await load_plan_account(conn, org_id, plan_account_id)
        p = await load_plan(conn, org_id, a["plan_id"])
        if p["status"] != "transferring" or a["status"] != "active":
            raise Section351Conflict("only a transferring plan's active account is confirmed")
        xs = [x for x in await transfers_of(conn, org_id, p["id"], account_id=a["account_id"])
              if x["status"] != "cancelled"]
        if not xs or any(x["status"] != "sent" for x in xs):
            raise Section351Conflict("every transfer instruction of the account must be sent before it is confirmed")
        if not await conn.fetchval("SELECT 1 FROM portfolio.securities_global WHERE id = $1::uuid AND system_to IS NULL "
                                   "LIMIT 1", etf):
            raise Section351NotFound("ETF security not found in the master")
        acct = await AM.load_account(conn, org_id, a["account_id"])
        co = R.carryover([{"tax_lot_id": str(x["tax_lot_id"]), "security_id": str(x["global_security_id"]),
                           "quantity": x["quantity"], "basis": x["basis"], "acquired_date": x["acquired_date"],
                           "fmv": x["fmv"]} for x in xs], etf_shares=etf_shares,
                         cash_to_member=Decimal(str(a["cash_to_member"])), on=received_on)
        async with conn.transaction():
            now = _now()
            moved: dict[str, tuple[Decimal, Decimal]] = {}
            for x, ln in zip(xs, co["lines"]):
                lot = await conn.fetchrow(
                    "SELECT tl.*, p.account_id, a.global_security_id FROM portfolio.tax_lots tl "
                    "JOIN portfolio.positions p ON p.id = tl.position_id JOIN portfolio.assets a ON a.id = p.asset_id "
                    "WHERE tl.id = $1 AND tl.org_id = $2::uuid AND tl.valid_to IS NULL AND tl.system_to IS NULL "
                    "FOR UPDATE OF tl", x["tax_lot_id"], str(org_id))
                if lot is None or lot["status"] != "open" or lot["account_id"] != a["account_id"] \
                        or Decimal(str(lot["quantity"])) != Decimal(str(x["quantity"])):
                    raise Section351Conflict(f"lot {x['tax_lot_id']} is no longer the open lot the plan contributed — "
                                             "re-test and re-approve")
                await conn.execute("UPDATE portfolio.tax_lots SET valid_to = now() WHERE id = $1", lot["id"])
                closed = await conn.fetchval(
                    """INSERT INTO portfolio.tax_lots (org_id, position_id, opening_transaction_id, acquired_date,
                           quantity, original_cost, adjusted_cost, term, realized_gain_loss, status, source_system,
                           external_ref)
                       VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, $8, NULL, 'closed', $9, $10) RETURNING id""",
                    str(org_id), lot["position_id"], lot["opening_transaction_id"], lot["acquired_date"],
                    lot["quantity"], lot["original_cost"], lot["adjusted_cost"], lot["term"],
                    lot["source_system"] or "section351", f"section351:transfer:{x['id']}")
                k = str(lot["global_security_id"])
                q0, b0 = moved.get(k, (ZERO, ZERO))
                moved[k] = (q0 + Decimal(str(lot["quantity"])), b0 + Decimal(str(x["basis"])))
                x["_closed"] = closed
            for k, (q, b) in moved.items():
                pos = await conn.fetchrow(
                    "SELECT p.* FROM portfolio.positions p JOIN portfolio.assets a ON a.id = p.asset_id "
                    "WHERE p.org_id = $1::uuid AND p.account_id = $2::uuid AND a.global_security_id = $3::uuid "
                    "AND p.valid_to IS NULL AND p.system_to IS NULL ORDER BY p.as_of_date DESC, p.system_from DESC "
                    "LIMIT 1", str(org_id), str(a["account_id"]), k)
                if pos is None:
                    continue
                left = Decimal(str(pos["quantity"] or 0)) - q
                if left < 0:
                    raise Section351Conflict("the position holds fewer shares than the lots transferred")
                ratio = left / Decimal(str(pos["quantity"])) if pos["quantity"] else ZERO
                await conn.execute("UPDATE portfolio.positions SET valid_to = now() WHERE id = $1", pos["id"])
                await conn.execute(
                    """INSERT INTO portfolio.positions (org_id, owner_entity_id, asset_id, as_of_date, ownership_basis,
                           quantity, cost_basis, market_value, authority, source_system, taxonomy_key, account_id)
                       VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)""",
                    str(org_id), pos["owner_entity_id"], pos["asset_id"], max(pos["as_of_date"], received_on),
                    pos["ownership_basis"], left, None if pos["cost_basis"] is None else
                    max(ZERO, Decimal(str(pos["cost_basis"])) - b),
                    None if pos["market_value"] is None else (Decimal(str(pos["market_value"])) * ratio).quantize(R.CENT),
                    pos["authority"], pos["source_system"], pos["taxonomy_key"], pos["account_id"])
            asset_id = await _asset_for(conn, org_id, etf)
            prior = await conn.fetchrow(
                "SELECT * FROM portfolio.positions WHERE org_id = $1::uuid AND account_id = $2::uuid AND asset_id = "
                "$3::uuid AND valid_to IS NULL AND system_to IS NULL ORDER BY as_of_date DESC LIMIT 1",
                str(org_id), str(a["account_id"]), asset_id)
            etf_basis = Decimal(co["totals"]["etf_basis"])
            etf_value = Decimal(co["totals"]["fmv"])
            if prior is not None:
                await conn.execute("UPDATE portfolio.positions SET valid_to = now() WHERE id = $1", prior["id"])
            pid = await conn.fetchval(
                """INSERT INTO portfolio.positions (org_id, owner_entity_id, asset_id, as_of_date, quantity, cost_basis,
                       market_value, authority, source_system, account_id)
                   VALUES ($1::uuid, $2::uuid, $3::uuid, $4, $5, $6, $7, 'manual', 'manual', $8::uuid) RETURNING id""",
                str(org_id), str(acct["primary_entity_id"]), asset_id, received_on,
                etf_shares + (Decimal(str(prior["quantity"] or 0)) if prior else ZERO),
                etf_basis + (Decimal(str(prior["cost_basis"] or 0)) if prior else ZERO) if (
                    prior is None or prior["cost_basis"] is not None) else None,
                etf_value + (Decimal(str(prior["market_value"] or 0)) if prior else ZERO), str(a["account_id"]))
            for x, ln in zip(xs, co["lines"]):
                etf_lot = await conn.fetchval(
                    """INSERT INTO portfolio.tax_lots (org_id, position_id, acquired_date, quantity, original_cost,
                           adjusted_cost, status, source_system, external_ref)
                       VALUES ($1::uuid, $2, $3, $4, $5, $5, 'open', 'section351', $6) RETURNING id""",
                    str(org_id), pid, date.fromisoformat(ln["etf_acquired_date"]), Decimal(ln["etf_shares"]),
                    Decimal(ln["etf_basis"]), f"section351:carryover:{x['id']}")
                await _update(conn, T_XFER, x["id"], {"status": "confirmed", "confirmed_by": actor["id"],
                                                      "confirmed_at": now, "received_on": received_on,
                                                      "closed_lot_id": x["_closed"], "etf_lot_id": etf_lot,
                                                      "etf_shares": Decimal(ln["etf_shares"]),
                                                      "realized_gain": ZERO})
            await _update(conn, T_ACC, a["id"], {"status": "transferred", "etf_global_security_id": uuid.UUID(etf),
                                                 "etf_shares": etf_shares, "received_on": received_on,
                                                 "confirmed_by": actor["id"], "confirmed_at": now})
            left_open = await conn.fetchval(f"SELECT count(*) FROM {T_ACC} WHERE plan_id = $1 AND status = 'active' "
                                            "AND system_to IS NULL AND valid_to IS NULL", p["id"])
            if left_open == 0:
                await _update(conn, T_PLAN, p["id"], {"status": "completed", "completed_at": now})
            await _audit(conn, org_id, actor["id"], "transfer_confirmed", SUBJECT_PLAN, p["id"],
                         {"plan_account_id": str(a["id"]), "lots": len(xs), "etf_shares": str(etf_shares),
                          "received_on": received_on.isoformat(), "realized_gain": "0"})
    try:
        from services import portfolio_refresh as PR

        await PR.after_write(pool, org_id, [acct["primary_entity_id"]], reason=f"section351 transfer {p['id']}",
                             actor_id=actor_id)
    except Exception:  # noqa: BLE001 — the nightly rollup picks it up
        log.exception("rollup after section351 transfer %s", p["id"])
    async with pool.acquire() as conn:
        return {"account": account_out(await load_plan_account(conn, org_id, plan_account_id)),
                "transfers": [transfer_out(x) for x in await transfers_of(conn, org_id, p["id"],
                                                                          account_id=a["account_id"])],
                "carryover": co}


# ═══ Reads ══════════════════════════════════════════════════════════════════════════════════════════════════════════
async def list_plans(conn, org_id) -> list[dict]:
    return [plan_out(dict(r)) for r in await conn.fetch(
        f"SELECT * FROM {T_PLAN} WHERE org_id = $1::uuid AND system_to IS NULL ORDER BY created_at DESC", str(org_id))]


async def detail(conn, org_id, plan_id) -> dict:
    p = await load_plan(conn, org_id, plan_id)
    accts = await plan_accounts(conn, p["id"], include_removed=True)
    masked = {str(r["id"]): r["account_number_masked"] for r in await conn.fetch(
        "SELECT id, account_number_masked FROM public.accounts WHERE id = ANY($1::uuid[]) AND system_to IS NULL AND "
        "valid_to IS NULL", [a["account_id"] for a in accts])}
    out_accts = []
    for a in accts:
        ao = account_out(a)
        ao["account"] = masked.get(str(a["account_id"]))
        if a["status"] != "removed":
            ao["disclosure"] = await disclosure_state(conn, org_id, a)
        out_accts.append(ao)
    prof = await profile_version(conn, org_id, p["sponsor_profile_id"])
    return {"plan": plan_out(p), "sponsor_profile": profile_out(prof), "accounts": out_accts,
            "readiness": await readiness(conn, org_id, p), "timeline": await timeline(conn, org_id, p["id"]),
            "basis_records": await basis_records(conn, org_id, p["id"]),
            "transfers": [transfer_out(x) for x in await transfers_of(conn, org_id, p["id"])],
            "open_items": list(OPEN_ITEMS), "label": R.LABEL}


async def addable_accounts(conn, org_id, plan_id) -> list[dict]:
    """Open accounts that follow a model (an active link), are not held-away and are not already in the plan."""
    return [{"account_id": str(r["id"]), "account": r["account_number_masked"], "custodian_code": r["custodian_code"]}
            for r in await conn.fetch(
                f"""SELECT a.id, a.account_number_masked, a.custodian_code FROM public.accounts a
                    WHERE a.org_id = $1::uuid AND a.system_to IS NULL AND a.valid_to IS NULL AND NOT a.is_held_away
                      AND (a.closed_on IS NULL OR a.closed_on > current_date)
                      AND EXISTS (SELECT 1 FROM public.account_model_links l WHERE l.account_id = a.id
                                    AND l.status = 'active' AND l.system_to IS NULL AND l.valid_to IS NULL)
                      AND NOT EXISTS (SELECT 1 FROM {T_ACC} x WHERE x.plan_id = $2::uuid AND x.account_id = a.id
                                        AND x.status <> 'removed' AND x.system_to IS NULL AND x.valid_to IS NULL)
                    ORDER BY a.account_number_masked, a.id""", str(org_id), str(plan_id))]


def plan_actions(d: dict, *, can_write: bool, can_approve: bool, user_id) -> list[str]:
    p, out = d["plan"], []
    s = p["status"]
    if can_write:
        if s == "draft":
            out += ["edit", "add_account", "mark_ready", "cancel"]
        if s == "ready":
            out += ["submit", "reopen", "cancel", "export_basis_record"]
        if s in ("pending_approval", "approved", "transferring", "completed"):
            out.append("export_basis_record")
        if s == "pending_approval":
            out.append("cancel")
        if s in ("approved", "transferring"):
            out += ["route_manual", "route_adapter", "instruction_sheet"]
        if s == "transferring":
            out.append("confirm_transfer")
        if s == "approved":
            out.append("cancel")
    me = str(user_id)
    if s == "pending_approval" and can_approve and me not in (p["maker_id"], p.get("submitted_by")):
        out += ["approve", "reject"]
    return out


def vocabularies(*, can_write: bool, can_admin: bool) -> dict:
    def pairs(d):
        return [{"key": k, "label": v} for k, v in d.items()]

    return {"owner_types": pairs(OWNER_LABELS), "asset_classes": pairs(ASSET_CLASS_LABELS),
            "cash_rules": pairs(CASH_RULE_LABELS), "plan_statuses": pairs(PLAN_STATUS_LABELS),
            "transfer_statuses": pairs(TRANSFER_STATUS_LABELS), "verdicts": pairs(R.VERDICT_LABELS),
            "steps": [{"key": k, "label": v, "default_days": d} for k, v, d in STEPS],
            "editable": (["plan", "accounts", "consent", "tax_adviser", "us_person", "boot", "acknowledge", "steps",
                          "asset_classes"] if can_write else []),
            "admin_editable": (["sponsor_profiles", "thresholds"] if can_admin else []),
            "inline_editable": [], "rules": R.RULE_NOTES, "label": R.LABEL, "open_items": list(OPEN_ITEMS)}
