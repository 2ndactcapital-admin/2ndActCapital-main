"""REP13 — the tax-aware LONG/SHORT extension (130/30, 150/50) on any custodian that declares margin and short
selling. The pure rules are services.long_short_rules (read its docstring first); this module is the database side.

WHO MAY GO LONG/SHORT (:func:`eligibility` — every refusal names the missing piece):
  * the account's custodian DECLARES margin, short_selling AND stock_borrow (public.custodian_capabilities;
    'unknown' is not declared — services.custodian_capabilities.available). No stored custodian declares any of
    them today, so no live account qualifies; the path is proven on a fixture custodian;
  * the account's effective authority is MANAGED with an INTEGRATED custodian (rep7b; read_only and advise refuse);
  * the member's suitability profile records an APPROVED margin agreement (date + a document) for the account's
    tax subject (its household, else its primary entity) — public.suitability_records, maker-checker;
  * the account is open and not held away.

A MODEL is long/short when portfolio.models.kind = 'long_short' (gross long / gross short, net 100 %, the signal
source and the as-of of its weights; named "Holdings of …"). An ACCOUNT on such a model needs its saved settings
(public.long_short_settings) with the same extension. Its proposals are made by a PERSON (rep5's manual trigger or
the join): the system's drift / model-change / harvest sweeps skip a long/short account. The proposal is rep5's
row (proposal_kind 'model') plus one public.long_short_proposals row with everything long/short adds.

THE GATES, in order: generation (eligibility, settings, extension match) → submit (the maker acknowledged the
disclosures) → approval precheck (a different person; eligibility re-read and refused if any piece lapsed since
the build; no blocking code — borrow rate needed, a §1259 block, tracking error over the limit; the §1259 check
re-run on today's household) → execution (the adapter of a custodian that does not declare short_selling refuses
a short at submit; the manual route is always offered) → every execution instruction that opens a short
(services.execution.create_instructions — the one chokepoint, a manual instruction included) re-checks
eligibility and §1259.

THE SHORT BOOK is DERIVED from execution fills (:func:`short_book`): each SELL SHORT fill opens a short lot (id =
the fill, proceeds per share = the fill price, opened = the fill date); each BUY TO COVER fill closes lots — the
instruction's named short lots first, then the oldest. The position is the negative sum. No custodian feeds short
positions today; until one does, this is the record (and it covers block allocations too, which book fills).
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from services import account_models as AM
from services import authority as AU
from services import disclosures as DL
from services import human_tasks as HT
from services import long_short_rules as R
from services import rebalance_engine as E
from services import wash_sale as WS

log = logging.getLogger(__name__)

VIEW, MANAGE, APPROVE = AM.VIEW, AM.MANAGE, AM.APPROVE
T_SUIT = "public.suitability_records"
T_SET = "public.long_short_settings"
T_RATE = "public.borrow_rates"
T_LSP = "public.long_short_proposals"
TASK_MARGIN = "margin_agreement_approval"
SUBJECT_MARGIN = "suitability_record"
REQUIRED_CAPS = ("margin", "short_selling", "stock_borrow")
CAP_LABELS = {"margin": "margin", "short_selling": "short selling", "stock_borrow": "stock borrow"}
READABLE_AUTHORITIES = ("read_only", "advise", "managed")   # every authority allows READING an account's data
OVERLAY_BPS_SETTING = "replication.long_short.overlay_bps"
TE_MIN_OBS_SETTING = "replication.long_short.te_min_observations"
DEFAULT_TE_MIN_OBS = 60
SETTING_FIELDS = ("extension", "gross_long", "gross_short", "tracking_error_limit", "max_short_per_name",
                  "borrow_cost_ceiling_bps", "exclusions", "note")
ACK_EXTRA = ("borrow_estimate", "margin_requirement", "member_summary")
ACK_ITEMS = R.RISK_KEYS + ACK_EXTRA
ACK_LABELS = {**dict(R.RISKS), "borrow_estimate": "The borrow-cost estimate",
              "margin_requirement": "The margin requirement", "member_summary": "The member-facing summary"}
#: DISCLOSURES1 — the risks come from the disclosure library (key long_short.risks, most specific version in force for
#: the account); with none in force, the built-in six (R.RISKS — the exact pre-library text).
RISKS_KEY = "long_short.risks"
ACK_SCREEN = "long_short.proposal"


def ack_items_of(risks: list[dict] | None) -> list[dict]:
    """The acknowledgement items for a set of shown risks ([{key, text}]) + the borrow / margin / summary items."""
    rs = risks or [{"key": k, "text": t} for k, t in R.RISKS]
    return [{"key": r["key"], "label": r["text"]} for r in rs] + [{"key": k, "label": ACK_LABELS[k]} for k in ACK_EXTRA]


async def library_risks(conn, org_id, account_id) -> dict:
    """The long/short risks in force for the account (library, else the built-in six) — never raises."""
    res = await DL.resolve_safe(conn, RISKS_KEY, org_id=org_id, account_id=account_id)
    items = res["items"] if res["items"] else [{"key": k, "text": t} for k, t in R.RISKS]
    return {**res, "items": items}


def _subject(p: dict) -> DL.Subject:
    return DL.Subject("long_short_proposal", str(p["id"]), str(p["account_id"]) if p.get("account_id") else None)


async def disclosure_state(conn, org_id, p: dict, user_id=None) -> dict:
    """Whether the maker's (or ``user_id``'s) acknowledgement names the long_short.risks version in force NOW. With no
    library version in force, rep13's own acknowledgement is the whole rule (``required`` False)."""
    who = user_id or p.get("maker_id")
    try:
        async with conn.transaction():
            st = await DL.ack_state(conn, org_id=org_id, user_id=who, key=RISKS_KEY, subject=_subject(p))
    except Exception as exc:  # noqa: BLE001 — the library unreadable: rep13's rule alone, flagged
        log.warning("long/short disclosure state: %s", exc)
        lib = await library_risks(conn, org_id, p.get("account_id"))
        return {"resolved": lib, "required": False, "acknowledged": None, "current": True, "degraded": True,
                "items": ack_items_of(lib["items"])}
    res = st["resolved"]
    items = res["items"] if res["items"] else [{"key": k, "text": t} for k, t in R.RISKS]
    return {**st, "items": ack_items_of(items)}
SUIT_STATUS_LABELS = {"pending_approval": "Awaiting a second person's approval", "approved": "Approved",
                      "rejected": "Rejected", "removed": "Removed"}
#: test seam — the verify replaces it with a fixture series reader at the same signature
#: ``await history_reader(conn, org_id, keys, today, window_days) -> (history, provenance)``
history_reader = None


class LongShortError(Exception):
    """A request the rules refuse (422)."""


class LongShortNotFound(LongShortError, LookupError):
    pass


class LongShortConflict(LongShortError):
    """The state does not allow it (409)."""


class LongShortNotEligible(LongShortConflict):
    def __init__(self, message: str, missing: list[str] | None = None):
        super().__init__(message)
        self.missing = list(missing or [])


class ConstructiveSaleBlocked(LongShortConflict):
    def __init__(self, message: str, hits: list[dict] | None = None):
        super().__init__(message)
        self.hits = list(hits or [])


class MakerCheckerViolation(PermissionError):
    pass


def _dumps(v) -> str:
    return json.dumps(v, default=lambda o: str(o) if isinstance(o, (Decimal, uuid.UUID)) else
                      o.isoformat() if isinstance(o, (date, datetime)) else str(o))


def _loads(v):
    if v is None or isinstance(v, (dict, list)):
        return v
    return json.loads(v)


def _uuid(v, what: str) -> str:
    try:
        return str(uuid.UUID(str(v)))
    except (ValueError, TypeError, AttributeError):
        raise LongShortNotFound(f"{what} not found") from None


def _dec(v) -> Decimal | None:
    return None if v is None else Decimal(str(v))


def _num(v):
    return None if v is None else str(v)


def _iso(v):
    return v.isoformat() if v else None


async def _audit(conn, org_id, user_id, action: str, resource_type: str, resource_id, payload: dict) -> None:
    await conn.execute(
        "INSERT INTO audit_log (org_id, user_id, action, resource_type, resource_id, payload) "
        "VALUES ($1::uuid, $2::uuid, $3, $4, $5::uuid, $6::jsonb)",
        str(org_id), str(user_id) if user_id else None, f"long_short.{action}", resource_type, str(resource_id),
        _dumps(payload))


async def _publish(pool, org_id, action: str, resource_id, payload: dict, actor_id) -> None:
    from services.domain_events import publish_event

    try:
        await publish_event(pool, str(org_id), f"long_short.{action}", "long_short", str(resource_id), payload)
    except Exception:  # noqa: BLE001 — an event never undoes the write it reports
        log.exception("long_short.%s event for %s", action, resource_id)


# ═══ Model kind ══════════════════════════════════════════════════════════════
async def model_kind(conn, model_id) -> dict | None:
    """The long/short facts of a model, or None for a long-only model."""
    r = await conn.fetchrow("SELECT kind, gross_long, gross_short, signal_source, signal_as_of, short_exclusions "
                            "FROM portfolio.models WHERE id = $1::uuid AND valid_to IS NULL AND system_to IS NULL",
                            str(model_id))
    if r is None or r["kind"] != "long_short":
        return None
    return {"kind": "long_short", "gross_long": r["gross_long"], "gross_short": r["gross_short"],
            "extension": R.extension_of(r["gross_long"], r["gross_short"]), "signal_source": r["signal_source"],
            "signal_as_of": r["signal_as_of"], "short_exclusions": [str(x) for x in (_loads(r["short_exclusions"]) or [])]}


def clean_model_spec(spec: dict | None) -> dict | None:
    """The long/short declaration of a NEW model (upload confirm): extension or custom pair, the signal source
    (sub-adviser name) and the as-of of its weights, the names the model never shorts."""
    if not spec:
        return None
    ext = str(spec.get("extension") or "")
    gl, gs = R.spec_of(ext, _dec(spec.get("gross_long")), _dec(spec.get("gross_short")))
    src = str(spec.get("signal_source") or "").strip()
    if not src:
        raise LongShortError("a long/short model records its signal source (the sub-adviser whose target weights "
                             "these are)")
    as_of = spec.get("signal_as_of")
    if isinstance(as_of, str):
        try:
            as_of = date.fromisoformat(as_of)
        except ValueError:
            raise LongShortError("signal as-of is a date (YYYY-MM-DD)") from None
    if not isinstance(as_of, date):
        raise LongShortError("a long/short model records the as-of date of its weights")
    if as_of > date.today():
        raise LongShortError("the weights' as-of date is in the future")
    excl = [_uuid(x, "security") for x in (spec.get("short_exclusions") or [])]
    return {"kind": "long_short", "gross_long": gl, "gross_short": gs, "signal_source": src[:300],
            "signal_as_of": as_of, "short_exclusions": excl}


async def short_info(conn, keys: list[str]) -> dict[str, dict]:
    """Whether each security may be shorted: a LISTED EQUITY in the master (security_type 'equity', not an ETF
    and not delisted by its reference data)."""
    keys = [str(k) for k in dict.fromkeys(keys) if k]
    if not keys:
        return {}
    rows = await conn.fetch(
        """SELECT g.id, g.name, g.security_type,
                  (SELECT array_agg(DISTINCT lower(a.value)) FROM portfolio.security_reference_attributes a
                    WHERE a.global_security_id = g.id AND a.field = 'listing_status' AND a.valid_to IS NULL
                      AND a.system_to IS NULL) AS listing,
                  (SELECT array_agg(DISTINCT lower(a.value)) FROM portfolio.security_reference_attributes a
                    WHERE a.global_security_id = g.id AND a.field = 'is_etf' AND a.valid_to IS NULL
                      AND a.system_to IS NULL) AS etf
           FROM portfolio.securities_global g WHERE g.id = ANY($1::uuid[]) AND g.valid_to IS NULL
             AND g.system_to IS NULL""", keys)
    out = {k: {"equity": False, "why": "not in the security master"} for k in keys}
    for r in rows:
        k = str(r["id"])
        listing = [x for x in (r["listing"] or []) if x]
        etf = [x for x in (r["etf"] or []) if x]
        if r["security_type"] != "equity":
            out[k] = {"equity": False, "why": f"a {r['security_type'].replace('_', ' ')}, not an equity"}
        elif "true" in etf:
            out[k] = {"equity": False, "why": "an exchange-traded fund, not a single equity"}
        elif listing and any(x != "listed" for x in listing):
            out[k] = {"equity": False, "why": f"its listing status is {', '.join(sorted(listing))}"}
        else:
            out[k] = {"equity": True, "why": None}
    return out


async def validate_model_rows(conn, rows: list, ls: dict, tolerance_pct: Decimal) -> list:
    """model_upload Rows of a long/short model → the upload's Message list (errors) for the long/short rules."""
    from services import model_upload as U

    wr = [R.WeightRow(row_num=r.row_num, key=r.global_security_id if r.state == "resolved" else None,
                      label=r.name or r.ticker or r.cusip or r.isin or f"row {r.row_num}", weight=r.weight,
                      is_cash=r.is_cash) for r in rows]
    shorts = [x.key for x in wr if x.key and x.weight is not None and x.weight < 0]
    info = await short_info(conn, shorts)
    errs = R.validate_weights(wr, gross_long=Decimal(ls["gross_long"]), gross_short=Decimal(ls["gross_short"]),
                              tolerance_pct=tolerance_pct, short_info=info,
                              exclusions=set(ls.get("short_exclusions") or []))
    return [U.Message(code, "error", text, row, "weight") for code, row, text in errs]


async def validate_version(conn, version_id, ls: dict, tolerance_pct: Decimal) -> list[tuple]:
    """A stored draft of a long/short model, re-validated at submit."""
    from services import models as M

    pos = await M.positions_of(conn, version_id)
    wr = [R.WeightRow(row_num=p["row_num"], key=str(p["global_security_id"]) if p["global_security_id"] else None,
                      label=p.get("security_name") or p.get("raw_name") or p.get("raw_ticker") or f"row {p['row_num']}",
                      weight=Decimal(str(p["target_weight"])), is_cash=p["resolution_state"] == "cash") for p in pos]
    info = await short_info(conn, [x.key for x in wr if x.key and x.weight < 0])
    return R.validate_weights(wr, gross_long=Decimal(ls["gross_long"]), gross_short=Decimal(ls["gross_short"]),
                              tolerance_pct=tolerance_pct, short_info=info,
                              exclusions=set(ls.get("short_exclusions") or []))


# ═══ Suitability: the margin agreement ═══════════════════════════════════════
def subject_of(account: dict) -> tuple[str, str]:
    from services.tax_profiles import subject_of as tax_subject

    return tax_subject(account)


async def margin_agreement(conn, org_id, account: dict) -> dict | None:
    st, sid = subject_of(account)
    r = await conn.fetchrow(
        f"SELECT * FROM {T_SUIT} WHERE org_id = $1::uuid AND subject_type = $2 AND subject_id = $3::uuid "
        "AND record_kind = 'margin_agreement' AND valid_to IS NULL AND system_to IS NULL "
        "AND status IN ('pending_approval', 'approved') ORDER BY created_at DESC LIMIT 1", str(org_id), st, sid)
    return dict(r) if r else None


def suitability_out(r: dict | None) -> dict | None:
    if not r:
        return None
    return {"id": str(r["id"]), "subject_type": r["subject_type"], "subject_id": str(r["subject_id"]),
            "record_kind": r["record_kind"], "agreement_date": _iso(r["agreement_date"]),
            "document_id": str(r["document_id"]), "note": r["note"], "status": r["status"],
            "status_label": SUIT_STATUS_LABELS[r["status"]], "maker_id": str(r["maker_id"]),
            "approved_by": str(r["approved_by"]) if r["approved_by"] else None,
            "approved_at": _iso(r["approved_at"]), "approval_task_id": str(r["approval_task_id"])
            if r["approval_task_id"] else None, "removed_reason": r["removed_reason"],
            "removed_at": _iso(r["removed_at"]), "created_at": _iso(r["created_at"])}


async def _suit_archive(conn, row_id) -> None:
    await conn.execute(
        f"""INSERT INTO {T_SUIT}
            SELECT (jsonb_populate_record(NULL::{T_SUIT}, to_jsonb(t) || jsonb_build_object(
                'id', gen_random_uuid(), 'archived_from_id', t.id, 'system_to', now()))).*
            FROM {T_SUIT} t WHERE t.id = $1::uuid AND t.system_to IS NULL""", str(row_id))


async def record_margin_agreement(pool, *, org_id, actor_id, account_id, agreement_date: date, document_id,
                                  note: str | None = None) -> dict:
    """Record the member's margin agreement (its date and the signed document) on the suitability profile. It
    takes effect only once a DIFFERENT holder of approve_proposals approves it (a human task)."""
    if not isinstance(agreement_date, date):
        raise LongShortError("the agreement date is a date")
    if agreement_date > date.today():
        raise LongShortError("the agreement date is in the future")
    async with pool.acquire() as conn:
        actor = await HT.assert_human(conn, actor_id, org_id)
        account = await AM.load_account(conn, org_id, account_id)
        doc = await conn.fetchval("SELECT id FROM public.documents WHERE id = $1::uuid AND org_id = $2::uuid",
                                  _uuid(document_id, "document"), str(org_id))
        if doc is None:
            raise LongShortNotFound("the document is not one of this organisation's documents")
        cur = await margin_agreement(conn, org_id, account)
        if cur is not None:
            raise LongShortConflict(f"a margin agreement is already {SUIT_STATUS_LABELS[cur['status']].lower()} for "
                                    "this member — remove it first to record another")
    st, sid = subject_of(account)
    rid = uuid.uuid4()
    task = await HT.open_task(pool, org_id=org_id, kind=TASK_MARGIN, subject_type=SUBJECT_MARGIN, subject_id=rid,
                              maker_id=actor["id"], eligible_permission=APPROVE,
                              title=f"Approve margin agreement ({account['account_number_masked']})",
                              payload={"account": account["account_number_masked"],
                                       "agreement_date": agreement_date.isoformat(), "document_id": str(doc)},
                              action_path=f"/portfolio/accounts/{account['id']}/long-short")
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    f"""INSERT INTO {T_SUIT} (id, org_id, subject_type, subject_id, record_kind, agreement_date,
                            document_id, note, status, maker_id, approval_task_id)
                        VALUES ($1, $2::uuid, $3, $4::uuid, 'margin_agreement', $5, $6::uuid, $7, 'pending_approval',
                                $8::uuid, $9::uuid) RETURNING *""",
                    rid, str(org_id), st, sid, agreement_date, str(doc), (note or "").strip() or None,
                    str(actor["id"]), task["id"])
                await _audit(conn, org_id, actor["id"], "margin_agreement_recorded", "suitability_record", rid,
                             {"account_id": str(account["id"]), "subject_type": st, "subject_id": sid,
                              "agreement_date": agreement_date.isoformat(), "document_id": str(doc),
                              "task_id": task["id"]})
    except Exception:
        await HT.cancel_task(pool, org_id=org_id, task_id=task["id"], actor_id=actor["id"],
                             reason="the margin agreement could not be recorded")
        raise
    await _publish(pool, org_id, "margin_agreement_recorded", rid, {"task_id": task["id"]}, actor["id"])
    return dict(row)


async def _apply_margin_outcome(pool, org_id, task: dict, outcome: str, actor_id, note) -> dict | None:
    async with pool.acquire() as conn:
        async with conn.transaction():
            r = await conn.fetchrow(f"SELECT * FROM {T_SUIT} WHERE approval_task_id = $1::uuid AND org_id = $2::uuid "
                                    "AND system_to IS NULL FOR UPDATE", task["id"], str(org_id))
            if r is None or r["status"] != "pending_approval":
                return dict(r) if r else None
            if str(r["maker_id"]) == str(actor_id):
                raise MakerCheckerViolation("maker-checker: the person who recorded the margin agreement cannot "
                                            "approve it")
            await _suit_archive(conn, r["id"])
            if outcome == "approve":
                new = await conn.fetchrow(f"UPDATE {T_SUIT} SET status = 'approved', approved_by = $2::uuid, "
                                          "approved_at = now(), decision_note = $3 WHERE id = $1 RETURNING *",
                                          r["id"], str(actor_id), note)
            else:
                new = await conn.fetchrow(f"UPDATE {T_SUIT} SET status = 'rejected', decision_note = $2, "
                                          "valid_to = now() WHERE id = $1 RETURNING *", r["id"], note)
            await _audit(conn, org_id, actor_id, f"margin_agreement_{'approved' if outcome == 'approve' else 'rejected'}",
                         "suitability_record", r["id"], {"task_id": task["id"], "note": note})
    return dict(new)


async def _on_margin_task(pool, task, outcome, actor_id, note):
    return await _apply_margin_outcome(pool, task["org_id"], task, outcome, actor_id, note)


HT.register_kind(TASK_MARGIN, label="Margin agreement approval", outcomes=("approve", "reject"), maker_checker=True,
                 on_complete=_on_margin_task)


async def decide_margin_agreement(pool, *, org_id, actor_id, record_id, outcome: str, note: str | None = None) -> dict:
    if outcome not in ("approve", "reject"):
        raise LongShortError("outcome is approve or reject")
    async with pool.acquire() as conn:
        actor = await HT.assert_human(conn, actor_id, org_id)
        r = await conn.fetchrow(f"SELECT * FROM {T_SUIT} WHERE id = $1::uuid AND org_id = $2::uuid "
                                "AND system_to IS NULL", _uuid(record_id, "suitability record"), str(org_id))
    if r is None:
        raise LongShortNotFound("suitability record not found")
    if r["status"] != "pending_approval":
        raise LongShortConflict(f"the margin agreement is {SUIT_STATUS_LABELS[r['status']].lower()}")
    if str(r["maker_id"]) == str(actor["id"]):
        raise MakerCheckerViolation("maker-checker: the person who recorded the margin agreement cannot approve it — "
                                    "a different holder of approve_proposals decides")
    try:
        done = await HT.complete_task(pool, org_id=org_id, task_id=r["approval_task_id"], actor_id=actor["id"],
                                      outcome=outcome, note=note)
    except HT.MakerCheckerError as exc:
        raise MakerCheckerViolation(str(exc)) from exc
    except HT.HumanTaskPermissionError as exc:
        raise PermissionError(str(exc)) from exc
    return done.get("subject_result") or dict(r)


async def remove_margin_agreement(pool, *, org_id, actor_id, account_id, reason: str) -> dict:
    """The agreement is withdrawn (lapsed, revoked, recorded in error). Takes effect at once — removing it only
    narrows what may be done; every long/short approval after it is refused. Audited; the record is kept."""
    if not (reason or "").strip():
        raise LongShortError("removing a margin agreement needs a reason")
    async with pool.acquire() as conn:
        actor = await HT.assert_human(conn, actor_id, org_id)
        account = await AM.load_account(conn, org_id, account_id)
        async with conn.transaction():
            cur = await margin_agreement(conn, org_id, account)
            if cur is None:
                raise LongShortNotFound("no margin agreement is on record for this member")
            if cur["status"] != "approved":
                raise LongShortConflict("a pending agreement is decided (approve / reject), not removed")
            await _suit_archive(conn, cur["id"])
            new = await conn.fetchrow(
                f"UPDATE {T_SUIT} SET status = 'removed', removed_by = $2::uuid, removed_at = now(), "
                "removed_reason = $3, valid_to = now() WHERE id = $1 RETURNING *", cur["id"], str(actor["id"]),
                reason.strip())
            await _audit(conn, org_id, actor["id"], "margin_agreement_removed", "suitability_record", cur["id"],
                         {"account_id": str(account["id"]), "reason": reason.strip()})
    await _publish(pool, org_id, "margin_agreement_removed", cur["id"], {}, actor["id"])
    return dict(new)


# ═══ Eligibility ═════════════════════════════════════════════════════════════
async def eligibility(conn, org_id, account: dict) -> dict:
    """Every piece, each with its own state; ``missing`` names what refuses (plain words)."""
    from services.custodian_capabilities import available, capability
    from services.custody.registry import trade_custodian_key

    key = trade_custodian_key(account.get("custodian_code")) or "manual"
    pieces, missing = [], []
    for cap in REQUIRED_CAPS:
        c = await capability(conn, key, cap)
        ok = available(c)
        pieces.append({"key": f"custodian_{cap}", "ok": ok, "label": f"Custodian declares {CAP_LABELS[cap]}",
                       "detail": f"{key}: {cap} is '{c.value}' ({c.evidence})"})
        if not ok:
            missing.append(f"the custodian ({key}) does not declare {CAP_LABELS[cap]} ({cap} is '{c.value}' — "
                           "unknown is not declared)")
    e = AU.effective(account)
    ok = e["authority"] == "managed" and e["custody"] == "integrated"
    pieces.append({"key": "authority", "ok": ok, "label": "Authority managed at an integrated custodian",
                   "detail": f"authority {AU.AUTHORITY_SHORT[e['authority']]}, custody {AU.CUSTODY_SHORT[e['custody']]}"
                             f" ({e['authority_source']})"})
    if not ok:
        missing.append(f"the account's authority is {AU.AUTHORITY_SHORT[e['authority']]} (custody "
                       f"{AU.CUSTODY_SHORT[e['custody']]}) — long/short needs managed at an integrated custodian")
    ma = await margin_agreement(conn, org_id, account)
    ok = ma is not None and ma["status"] == "approved"
    pieces.append({"key": "margin_agreement", "ok": ok, "label": "Margin agreement on the suitability profile",
                   "detail": ("approved — dated " + ma["agreement_date"].isoformat()) if ok else
                   (f"{SUIT_STATUS_LABELS[ma['status']].lower()}" if ma else "none recorded"),
                   "record": suitability_out(ma)})
    if not ok:
        missing.append("no approved margin agreement is recorded on the member's suitability profile"
                       + (" (one awaits approval)" if ma else ""))
    if account.get("is_held_away"):
        missing.append("the account is held away — never traded")
    if AM.is_closed(account):
        missing.append("the account is closed")
    return {"eligible": not missing, "custodian": key, "pieces": pieces, "missing": missing,
            "refusal": None if not missing else "not eligible for long/short: " + "; ".join(missing) + "."}


async def require_eligible(conn, org_id, account: dict, what: str) -> dict:
    el = await eligibility(conn, org_id, account)
    if not el["eligible"]:
        raise LongShortNotEligible(f"{account['account_number_masked']}: {what} refused — {el['refusal']}",
                                   el["missing"])
    return el


# ═══ Account settings (draft → saved; history) ═══════════════════════════════
async def settings_of(conn, account_id, status: str = "saved") -> dict | None:
    r = await conn.fetchrow(f"SELECT * FROM {T_SET} WHERE account_id = $1::uuid AND status = $2 AND valid_to IS NULL "
                            "AND system_to IS NULL", str(account_id), status)
    return dict(r) if r else None


def settings_out(r: dict | None) -> dict | None:
    if not r:
        return None
    return {"id": str(r["id"]), "status": r["status"], "version_no": r["version_no"], "extension": r["extension"],
            "extension_label": R.EXTENSION_LABELS[r["extension"]], "gross_long": str(r["gross_long"]),
            "gross_short": str(r["gross_short"]), "tracking_error_limit": str(r["tracking_error_limit"]),
            "max_short_per_name": str(r["max_short_per_name"]),
            "borrow_cost_ceiling_bps": str(r["borrow_cost_ceiling_bps"]), "exclusions": _loads(r["exclusions"]) or [],
            "note": r["note"], "created_by": str(r["created_by"]), "saved_by": str(r["saved_by"]) if r["saved_by"]
            else None, "saved_at": _iso(r["saved_at"]), "valid_from": _iso(r["valid_from"]),
            "valid_to": _iso(r["valid_to"])}


async def settings_history(conn, account_id) -> list[dict]:
    rows = await conn.fetch(f"SELECT * FROM {T_SET} WHERE account_id = $1::uuid AND status = 'saved' "
                            "AND system_to IS NULL ORDER BY version_no DESC", str(account_id))
    return [settings_out(dict(r)) for r in rows]


def clean_settings(raw: dict) -> dict:
    unknown = set(raw) - set(SETTING_FIELDS)
    if unknown:
        raise LongShortError(f"not a long/short setting: {sorted(unknown)}")
    ext = str(raw.get("extension") or "")
    gl, gs = R.spec_of(ext, raw.get("gross_long"), raw.get("gross_short"))

    def frac(name, lo, hi, *, lo_open=True):
        v = raw.get(name)
        if not isinstance(v, Decimal):
            raise LongShortError(f"{name} is a decimal fraction (0.05 = 5%)")
        if (v <= lo if lo_open else v < lo) or v > hi:
            raise LongShortError(f"{name} must be above {lo} and at most {hi}")
        return v

    te = frac("tracking_error_limit", Decimal(0), Decimal(1))
    mx = frac("max_short_per_name", Decimal(0), R.MAX_GROSS_SHORT)
    ceil = raw.get("borrow_cost_ceiling_bps")
    if not isinstance(ceil, Decimal) or ceil < 0 or ceil > 10000:
        raise LongShortError("borrow_cost_ceiling_bps is a decimal number of basis points a year (0–10,000)")
    excl, seen = [], set()
    for x in raw.get("exclusions") or []:
        sid = _uuid(x.get("global_security_id") if isinstance(x, dict) else x, "security")
        if sid in seen:
            raise LongShortError("a security is excluded twice")
        seen.add(sid)
        why = (x.get("reason") if isinstance(x, dict) else None) or "never shorted in this account"
        excl.append({"global_security_id": sid, "reason": str(why).strip()[:500]})
    return {"extension": ext, "gross_long": gl, "gross_short": gs, "tracking_error_limit": te,
            "max_short_per_name": mx, "borrow_cost_ceiling_bps": ceil, "exclusions": excl,
            "note": (str(raw.get("note") or "").strip() or None)}


async def save_settings(pool, *, org_id, actor_id, account_id, values: dict, status: str) -> dict:
    """``status`` 'draft' (work in progress, replaces the live draft) or 'saved' (a new version; closes the live
    saved row and any draft). Only on an ELIGIBLE account. Every write is audited; rows are only ever closed."""
    if status not in ("draft", "saved"):
        raise LongShortError("status is draft or saved")
    clean = clean_settings(values)
    async with pool.acquire() as conn:
        actor = await HT.assert_human(conn, actor_id, org_id)
        account = await AM.load_account(conn, org_id, account_id)
        await require_eligible(conn, org_id, account, "long/short settings")
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"long_short_settings:{account['id']}")
            saved = await settings_of(conn, account["id"], "saved")
            draft = await settings_of(conn, account["id"], "draft")
            if status == "draft" and draft:
                await conn.execute(f"UPDATE {T_SET} SET valid_to = clock_timestamp() WHERE id = $1", draft["id"])
            if status == "saved":
                for cur in (saved, draft):
                    if cur:
                        await conn.execute(f"UPDATE {T_SET} SET valid_to = clock_timestamp() WHERE id = $1", cur["id"])
            version = (saved["version_no"] + 1) if saved else 1
            if status == "draft":
                version = draft["version_no"] if draft else version
            row = await conn.fetchrow(
                f"""INSERT INTO {T_SET} (org_id, account_id, status, version_no, extension, gross_long, gross_short,
                        tracking_error_limit, max_short_per_name, borrow_cost_ceiling_bps, exclusions, note,
                        created_by, saved_by, saved_at, valid_from)
                    VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, $7, $8, $9, $10, $11::jsonb, $12, $13::uuid,
                            $14::uuid, $15, clock_timestamp()) RETURNING *""",
                str(org_id), str(account["id"]), status, version, clean["extension"], clean["gross_long"],
                clean["gross_short"], clean["tracking_error_limit"], clean["max_short_per_name"],
                clean["borrow_cost_ceiling_bps"], _dumps(clean["exclusions"]), clean["note"], str(actor["id"]),
                str(actor["id"]) if status == "saved" else None,
                datetime.now(timezone.utc) if status == "saved" else None)
            await _audit(conn, org_id, actor["id"], f"settings_{status}", "long_short_settings", row["id"],
                         {"account_id": str(account["id"]), "version_no": version,
                          "previous_id": str(saved["id"]) if saved and status == "saved" else None,
                          **{k: (str(v) if isinstance(v, Decimal) else v) for k, v in clean.items()}})
    await _publish(pool, org_id, f"settings_{status}", row["id"], {"account_id": str(account["id"])}, actor["id"])
    return dict(row)


# ═══ Borrow rates (entered) ══════════════════════════════════════════════════
async def enter_borrow_rate(pool, *, org_id, actor_id, is_super_admin: bool, scope: str, global_security_id,
                            rate_bps: Decimal, as_of: date, source: str, rebate_bps: Decimal | None = None) -> dict:
    """A rate per security with its as-of. 'platform' = Super Admin (every org reads it); 'org' = this org's own
    (e.g. its prime broker's quote) — read first. A correction on the same as-of closes the old row."""
    if scope not in ("platform", "org"):
        raise LongShortError("scope is platform or org")
    if scope == "platform" and not is_super_admin:
        raise PermissionError("Super Admin required to enter a platform borrow rate")
    for name, v in (("rate_bps", rate_bps), ("rebate_bps", rebate_bps)):
        if v is not None and (not isinstance(v, Decimal) or v < 0):
            raise LongShortError(f"{name} is a decimal number of basis points a year, zero or more")
    if rate_bps is None:
        raise LongShortError("a borrow rate is required")
    if not isinstance(as_of, date) or as_of > date.today():
        raise LongShortError("the rate's as-of is a date, not in the future")
    if not (source or "").strip():
        raise LongShortError("name the rate's source (e.g. the prime broker's locate sheet)")
    async with pool.acquire() as conn:
        actor = await HT.assert_human(conn, actor_id, org_id)
        sec = await conn.fetchval("SELECT id FROM portfolio.securities_global WHERE id = $1::uuid "
                                  "AND valid_to IS NULL AND system_to IS NULL", _uuid(global_security_id, "security"))
        if sec is None:
            raise LongShortNotFound("security not found")
        async with conn.transaction():
            if scope == "platform":
                await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
            oid = None if scope == "platform" else str(org_id)
            old = await conn.fetchval(
                f"SELECT id FROM {T_RATE} WHERE owner_scope = $1 AND org_id IS NOT DISTINCT FROM $2::uuid "
                "AND global_security_id = $3::uuid AND as_of = $4 AND valid_to IS NULL AND system_to IS NULL",
                scope, oid, str(sec), as_of)
            if old:
                await conn.execute(f"UPDATE {T_RATE} SET valid_to = clock_timestamp() WHERE id = $1", old)
            row = await conn.fetchrow(
                f"INSERT INTO {T_RATE} (owner_scope, org_id, global_security_id, rate_bps, rebate_bps, as_of, source, "
                "entered_by) VALUES ($1, $2::uuid, $3::uuid, $4, $5, $6, $7, $8::uuid) RETURNING *",
                scope, oid, str(sec), rate_bps, rebate_bps, as_of, source.strip()[:300], str(actor["id"]))
            await _audit(conn, org_id, actor["id"], "borrow_rate_entered", "borrow_rate", row["id"],
                         {"scope": scope, "global_security_id": str(sec), "rate_bps": str(rate_bps),
                          "rebate_bps": _num(rebate_bps), "as_of": as_of.isoformat(),
                          "replaces": str(old) if old else None})
    return dict(row)


async def borrow_rates_for(conn, org_id, keys: list[str], today: date) -> dict[str, R.Borrow]:
    """The rate for each security: this org's latest (as-of ≤ today), else the platform's. Absent = none."""
    keys = [str(k) for k in dict.fromkeys(keys) if k]
    if not keys:
        return {}
    rows = await conn.fetch(
        f"""SELECT DISTINCT ON (global_security_id) id, global_security_id, owner_scope, rate_bps, rebate_bps, as_of,
                   source
            FROM {T_RATE} WHERE global_security_id = ANY($1::uuid[]) AND as_of <= $3 AND valid_to IS NULL
              AND system_to IS NULL AND (owner_scope = 'platform' OR org_id = $2::uuid)
            ORDER BY global_security_id, (owner_scope = 'org') DESC, as_of DESC, created_at DESC""",
        keys, str(org_id), today)
    return {str(r["global_security_id"]): R.Borrow(Decimal(str(r["rate_bps"])), _dec(r["rebate_bps"]), r["as_of"],
                                                   r["source"], r["owner_scope"], str(r["id"])) for r in rows}


def rate_out(r: dict) -> dict:
    return {"id": str(r["id"]), "scope": r["owner_scope"], "global_security_id": str(r["global_security_id"]),
            "security_name": r.get("security_name"), "rate_bps": str(r["rate_bps"]),
            "rebate_bps": _num(r["rebate_bps"]), "as_of": _iso(r["as_of"]), "source": r["source"],
            "entered_by": str(r["entered_by"]), "created_at": _iso(r["created_at"])}


async def list_borrow_rates(conn, org_id) -> list[dict]:
    rows = await conn.fetch(
        f"""SELECT r.*, g.name AS security_name FROM {T_RATE} r
            LEFT JOIN portfolio.securities_global g ON g.id = r.global_security_id AND g.valid_to IS NULL
              AND g.system_to IS NULL
            WHERE r.valid_to IS NULL AND r.system_to IS NULL AND (r.owner_scope = 'platform' OR r.org_id = $1::uuid)
            ORDER BY g.name NULLS LAST, r.as_of DESC""", str(org_id))
    return [rate_out(dict(r)) for r in rows]


# ═══ The short book (derived from fills) ═════════════════════════════════════
SIDE_OPEN, SIDE_CLOSE = "sell_short", "buy_to_cover"


async def short_book(conn, org_id, account_ids: list[str]) -> tuple[dict[str, dict[str, R.ShortPosition]], list]:
    """{account_id: {security: ShortPosition (no price yet)}} and the realized covers [{account_id, key, on,
    quantity, price, gain, term, lots}] — every SELL SHORT / BUY TO COVER fill of these accounts, in fill order."""
    ids = [str(a) for a in account_ids]
    if not ids:
        return {}, []
    rows = await conn.fetch(
        """SELECT f.id AS fill_id, f.quantity, f.price, f.filled_at, i.id AS instruction_id, i.side, i.account_id,
                  i.global_security_id, i.short_terms, g.name AS security_name
           FROM public.execution_fills f JOIN public.execution_instructions i ON i.id = f.instruction_id
                AND i.system_to IS NULL
           LEFT JOIN portfolio.securities_global g ON g.id = i.global_security_id AND g.valid_to IS NULL
                AND g.system_to IS NULL
           WHERE f.org_id = $1::uuid AND i.account_id = ANY($2::uuid[]) AND i.side = ANY($3::text[])
             AND f.valid_to IS NULL AND f.system_to IS NULL
           ORDER BY f.filled_at, f.created_at, f.id""", str(org_id), ids, [SIDE_OPEN, SIDE_CLOSE])
    book: dict[str, dict[str, R.ShortPosition]] = {}
    realized = []
    for r in rows:
        a, k = str(r["account_id"]), str(r["global_security_id"])
        pos = book.setdefault(a, {}).get(k)
        if pos is None:
            pos = book[a][k] = R.ShortPosition(k, r["security_name"] or k, Decimal(0), None, None, "none", [])
        q, px, on = Decimal(str(r["quantity"])), Decimal(str(r["price"])), r["filled_at"].date()
        if r["side"] == SIDE_OPEN:
            pos.lots.append(R.ShortLot(str(r["fill_id"]), q, px, on, a))
            pos.quantity += q
            continue
        named = [(x.get("short_lot_id"), Decimal(str(x["quantity"])))
                 for x in ((_loads(r["short_terms"]) or {}).get("lots") or []) if x.get("short_lot_id")]
        try:
            relief = R.relieve_short_lots([x for x in pos.lots if x.quantity > 0], q, named)
        except R.LongShortRuleError:
            relief = R.relieve_short_lots([x for x in pos.lots if x.quantity > 0], min(q, pos.quantity)) \
                if pos.quantity > 0 else []
        gain = R.cover_gain(relief, px)
        for lot, qq in relief:
            lot.quantity -= qq
        pos.quantity -= sum((qq for _, qq in relief), Decimal(0))
        pos.lots = [x for x in pos.lots if x.quantity > 0]
        realized.append({"account_id": a, "key": k, "label": pos.label, "on": on, "quantity": q, "price": px,
                         "gain": gain, "term": R.short_close_term(min((lot.opened for lot, _ in relief), default=on), on),
                         "lots": [{"short_lot_id": lot.id, "quantity": str(qq), "opened": lot.opened.isoformat(),
                                   "open_price": str(lot.open_price)} for lot, qq in relief],
                         "fill_id": str(r["fill_id"])})
    for a in list(book):
        book[a] = {k: v for k, v in book[a].items() if v.quantity > 0}
    return book, realized


async def price_shorts(conn, org_id, positions: dict[str, R.ShortPosition], today: date) -> None:
    from services import rebalance_proposals as RP

    if not positions:
        return
    stale = await RP._setting(conn, org_id, RP.STALE_SETTING, RP.DEFAULT_STALE_DAYS, lo=0, hi=366, integer=True)
    px = await RP._unheld_prices(conn, sorted(positions), today - timedelta(days=stale), org_id=org_id, today=today)
    for k, x in positions.items():
        if k in px:
            x.price, x.price_as_of, x.price_source = px[k]


# ═══ §1259 — the household's long lots and "substantially identical" ═════════
async def issuer_facts(conn, keys: list[str]) -> dict[str, dict]:
    keys = [str(k) for k in dict.fromkeys(keys) if k]
    if not keys:
        return {}
    rows = await conn.fetch(
        """SELECT g.id, g.security_type, g.canonical_id,
                  (SELECT min(a.value) FROM portfolio.security_reference_attributes a WHERE a.global_security_id = g.id
                     AND a.field = 'cik' AND a.valid_to IS NULL AND a.system_to IS NULL) AS cik
           FROM portfolio.securities_global g WHERE g.id = ANY($1::uuid[]) AND g.valid_to IS NULL
             AND g.system_to IS NULL""", keys)
    return {str(r["id"]): {"type": r["security_type"], "canonical": str(r["canonical_id"]) if r["canonical_id"] else None,
                           "cik": (r["cik"] or "").lstrip("0") or None} for r in rows}


def identical_fn(facts: dict[str, dict], denials: dict):
    """(a, b) → the reason they are substantially identical for §1259, or None."""
    def fn(a: str, b: str) -> str | None:
        if a == b:
            return "the same security"
        fa, fb = facts.get(a) or {}, facts.get(b) or {}
        if fa.get("canonical") and fa.get("canonical") == fb.get("canonical") and fa["canonical"] not in (a, b) \
                or fa.get("canonical") == b or fb.get("canonical") == a:
            return "the same canonical security in the master"
        if fa.get("type") == "equity" and fb.get("type") == "equity" and fa.get("cik") and fa.get("cik") == fb.get("cik"):
            return f"another share class of the same issuer (SEC CIK {fa['cik']})"
        d = denials.get(frozenset((a, b)))
        if d is not None:
            return f"a denied substitution pair ({d.get('rule', 'denial')}: {d.get('reason') or 'substantially identical'})"
        return None
    return fn


async def household_long_lots(conn, org_id, account: dict, today: date) -> tuple[list[R.LongLot], list[dict]]:
    """Every open long lot of every account of the household (rep5's household_accounts: household, primary
    entity, shared owners — IRAs and held-away accounts included), priced by each account's own position (else a
    market close). Returns (lots, accounts read)."""
    from services import rebalance_proposals as RP

    accts = await RP.household_accounts(conn, org_id, account)
    lots, read = [], []
    for a in accts:
        full = await AM.load_account(conn, org_id, a["id"])      # the effective authority rides on it (rep7b)
        e = AU.effective(full)
        if e["authority"] not in READABLE_AUTHORITIES:
            read.append({"account_id": str(a["id"]), "account": a["account_number_masked"], "read": False,
                         "why": f"authority {e['authority']} does not allow reading"})
            continue
        hold = await AM.load_holdings(conn, org_id, a["id"])
        unpriced = [h.key for h in hold["holdings"] if h.price is None]
        px = await RP._unheld_prices(conn, unpriced, today - timedelta(days=30), org_id=org_id, today=today) \
            if unpriced else {}
        n = 0
        for h in hold["holdings"]:
            price = h.price if h.price is not None else (px.get(h.key) or (None,))[0]
            for lot in h.lots:
                lots.append(R.LongLot(str(a["id"]), a["account_number_masked"], bool(a["is_held_away"]), h.key,
                                      h.label, lot.id, lot.quantity, lot.unit_cost, price, lot.acquired))
                n += 1
        read.append({"account_id": str(a["id"]), "account": a["account_number_masked"], "read": True,
                     "held_away": bool(a["is_held_away"]), "lots": n})
    return lots, read


async def constructive_sale_check(conn, org_id, account: dict, short_keys: list[str], today: date
                                  ) -> tuple[dict[str, list[dict]], list[dict]]:
    """{short key: [hits]} for every short key a household long lot blocks, and the accounts read."""
    if not short_keys:
        return {}, []
    lots, read = await household_long_lots(conn, org_id, account, today)
    facts = await issuer_facts(conn, list(short_keys) + [x.key for x in lots])
    ident = identical_fn(facts, await WS.active_denials(conn, org_id))
    out = {}
    for k in short_keys:
        hits = R.constructive_sale_hits(str(k), lots, ident)
        if hits:
            out[str(k)] = hits
    return out, read


async def household_cover_events(conn, org_id, account: dict, today: date) -> list[R.CoverEvent]:
    from services import rebalance_proposals as RP

    accts = await RP.household_accounts(conn, org_id, account)
    by = {str(a["id"]): a["account_number_masked"] for a in accts}
    _, realized = await short_book(conn, org_id, list(by))
    return [R.CoverEvent(x["key"], x["on"], x["gain"] < 0, by.get(x["account_id"], ""), x["fill_id"])
            for x in realized if x["on"] >= today - timedelta(days=R.WASH_DAYS)]


# ═══ The proposal (called by rebalance_proposals._generate) ══════════════════
async def compute(conn, org_id, account: dict, link: dict, version_id, model: dict, ls: dict, *,
                  today: date | None = None) -> dict:
    """Everything a long/short proposal stores (no writes) — the same shape as rebalance_proposals.compute, plus
    ``short_legs`` and ``long_short`` (the details row)."""
    from services import harvest as HV
    from services import models as M
    from services import rebalance_proposals as RP
    from services import tax_profiles as TP
    from services.custodian_capabilities import available, capability
    from services.custody.registry import trade_custodian_key

    today = today or date.today()
    el = await require_eligible(conn, org_id, account, "a long/short proposal")
    st = await settings_of(conn, account["id"], "saved")
    if st is None:
        raise LongShortConflict(f"{account['account_number_masked']}: save the account's long/short settings "
                                "(extension, tracking-error limit, maximum short per name, borrow-cost ceiling, "
                                "exclusions) before a proposal")
    if (Decimal(st["gross_long"]), Decimal(st["gross_short"])) != (Decimal(ls["gross_long"]), Decimal(ls["gross_short"])):
        raise LongShortConflict(f"the account's extension is {R.EXTENSION_LABELS[st['extension']].split(' —')[0]} but "
                                f"{model['name']} is {R.pct_text(Decimal(ls['gross_long']))} long / "
                                f"{R.pct_text(Decimal(ls['gross_short']))} short — change the settings or the model")
    hold = await AM.load_holdings(conn, org_id, account["id"])
    pos = await M.positions_of(conn, version_id)
    target: dict[str, Decimal] = {}
    for p in pos:
        k = E.CASH if p["resolution_state"] == "cash" else (str(p["global_security_id"]) if p["global_security_id"]
                                                             else None)
        if k is None:
            continue
        target[k] = target.get(k, Decimal(0)) + Decimal(str(p["target_weight"]))
    # the link's (rep5) exclusions: a long name excluded from the account goes to cash; never re-allocated by guess
    link_excl = {str(x["global_security_id"]): x.get("reason") or "excluded"
                 for x in (_loads(link.get("exclusions")) or []) if isinstance(x, dict) and x.get("global_security_id")}
    warnings: list[dict] = []
    for k, why in link_excl.items():
        if target.get(k, Decimal(0)) > 0:
            target[E.CASH] = target.get(E.CASH, Decimal(0)) + target.pop(k)
            warnings.append({"code": "exclusion_note", "message": f"{k}: excluded from this account ({why}) — its "
                             "long weight is held in cash"})
    book, _ = await short_book(conn, org_id, [str(account["id"])])
    shorts = book.get(str(account["id"]), {})
    await price_shorts(conn, org_id, shorts, today)
    keys = sorted(set(target) - {E.CASH} | {h.key for h in hold["holdings"]} | set(shorts))
    labels = await WS.security_labels(conn, keys)
    for h in hold["holdings"]:
        labels.setdefault(h.key, h.label)
    stale = await RP._setting(conn, org_id, RP.STALE_SETTING, RP.DEFAULT_STALE_DAYS, lo=0, hi=366, integer=True)
    unheld = [k for k in keys if k not in {h.key for h in hold["holdings"]} and k not in shorts]
    prices = await RP._unheld_prices(conn, unheld, today - timedelta(days=stale), org_id=org_id, today=today)
    ctx = await WS.load_context(conn, org_id, account, today)
    key = trade_custodian_key(account.get("custodian_code")) or "manual"
    frac = await capability(conn, key, "fractional_shares")
    min_trade = await RP._setting(conn, org_id, RP.MIN_TRADE_SETTING, RP.DEFAULT_MIN_TRADE, lo=Decimal(0))
    hv = await HV.load_settings(conn, org_id, link)
    excl = {str(x["global_security_id"]): x.get("reason") or "never shorted" for x in _loads(st["exclusions"]) or []}
    for k in ls.get("short_exclusions") or []:
        excl.setdefault(str(k), "on the model's never-short list")
    candidates = sorted({k for k, w in target.items() if k != E.CASH and w < 0} | set(shorts))
    subs = {k: [c for c in ctx.substitutes.get(k, [])] for k in set(candidates) | {h.key for h in hold["holdings"]}}
    sub_keys = sorted({c["key"] for v in subs.values() for c in v})
    all_short_keys = sorted(set(candidates) | set(sub_keys))
    borrow = await borrow_rates_for(conn, org_id, all_short_keys, today)
    blocks, read = await constructive_sale_check(conn, org_id, account, all_short_keys, today)
    covers = await household_cover_events(conn, org_id, account, today)
    facts = await issuer_facts(conn, all_short_keys + [c.key for c in covers])
    denials = await WS.active_denials(conn, org_id)
    ident = identical_fn(facts, denials)
    washes = {}
    for k in all_short_keys:
        r = R.short_wash(k, covers, today, ident)
        if r:
            washes[k] = r
    if sub_keys:
        sub_prices = await RP._unheld_prices(conn, [k for k in sub_keys if k not in prices and k not in shorts
                                                    and k not in {h.key for h in hold["holdings"]}],
                                             today - timedelta(days=stale), org_id=org_id, today=today)
        prices.update(sub_prices)
        for k in sub_keys:
            labels.setdefault(k, ctx.label(k))
    sinfo = await short_info(conn, all_short_keys)
    ceiling = Decimal(str(st["borrow_cost_ceiling_bps"]))

    # ── harvest, both books ─────────────────────────────────────────────────
    picks: list[R.HarvestPick] = []
    harvest_log: list[dict] = []
    profile = await TP.profile_for_account(conn, org_id, account, today.year)
    rates = TP.combined_rates(profile)
    if link.get("harvest_enabled"):
        for k, x in shorts.items():
            u = x.unrealized
            if u is None or u >= 0 or target.get(k, Decimal(0)) >= 0:
                continue
            basis = sum((lot.open_price * lot.quantity for lot in x.lots), Decimal(0))
            tcost = x.market_value * hv["trading_cost_bps"] / R.BPS * 2
            ok, net, lp = R.harvest_passes(u, basis, tcost, pct=hv["loss_pct"], minimum=hv["loss_min"])
            entry = {"book": "short", "key": k, "label": x.label, "loss": str(R.cents(u)), "net_loss": str(R.cents(net)),
                     "loss_pct": str(lp), "term": "short"}
            if not ok:
                harvest_log.append({**entry, "outcome": "below_threshold"})
                continue
            sub, tried = None, []
            for c in subs.get(k, []):
                s_ = c["key"]
                why = (("identical — " + ident(s_, k)) if ident(s_, k) else
                       "excluded" if s_ in excl else
                       "not a listed equity" if not (sinfo.get(s_) or {}).get("equity") else
                       "no borrow rate" if s_ not in borrow else
                       "borrow above the ceiling" if borrow[s_].rate_bps > ceiling else
                       "§1259 block" if s_ in blocks else
                       "short-sale wash window" if s_ in washes else
                       "no price" if s_ not in prices else None)
                if why is None:
                    sub = c
                    break
                tried.append({"security_id": s_, "label": c["label"], "why_not": why})
            if sub is None and not hv["to_cash"]:
                harvest_log.append({**entry, "outcome": "no_substitute", "tried": tried})
                continue
            picks.append(R.HarvestPick("short", k, x.label, sub["key"] if sub else None,
                                       sub["label"] if sub else None, u, net, "short",
                                       "short at a loss — covered and re-established" if sub else "covered to cash"))
            harvest_log.append({**entry, "outcome": "proposed", "substitute": sub["key"] if sub else None,
                                "substitute_label": sub["label"] if sub else None, "tried": tried,
                                "est_tax_value": None if rates is None else str(R.cents(-net * rates["st"])),
                                "wash_rule": "the re-established short is a name that is not substantially "
                                             "identical and has no household cover at a loss within 30 days"})
        acls = WS.tax_class(account)
        for h in hold["holdings"]:
            if h.price is None or target.get(h.key, Decimal(0)) <= 0 or not h.lots \
                    or any(lot.unit_cost is None for lot in h.lots):
                continue
            u = sum(((h.price - lot.unit_cost) * lot.quantity for lot in h.lots), Decimal(0))
            if u >= 0:
                continue
            basis = sum((lot.unit_cost * lot.quantity for lot in h.lots), Decimal(0))
            tcost = h.market_value * hv["trading_cost_bps"] / R.BPS * 2
            ok, net, lp = R.harvest_passes(u, basis, tcost, pct=hv["loss_pct"], minimum=hv["loss_min"])
            term = "long" if all(E.term_of(lot, today) == "long" for lot in h.lots) else "short"
            entry = {"book": "long", "key": h.key, "label": h.label, "loss": str(R.cents(u)),
                     "net_loss": str(R.cents(net)), "loss_pct": str(lp), "term": term}
            if not ok:
                harvest_log.append({**entry, "outcome": "below_threshold"})
                continue
            if WS.sale_restriction(h.key, account_class=acls, ctx=ctx):
                harvest_log.append({**entry, "outcome": "wash_held"})
                continue
            own = [WS.Event(h.key, "sell", today, today, True, str(account["id"]), account["account_number_masked"],
                            acls, "this_proposal")]
            sub, tried = None, []
            for c in subs.get(h.key, []):
                r = WS.evaluate_buy(c["key"], account_class=acls, ctx=ctx, own=own)
                if r["outcome"] == "clear" and not ident(c["key"], h.key) and target.get(c["key"], Decimal(0)) >= 0:
                    sub = c
                    break
                tried.append({"security_id": c["key"], "label": c["label"], "why_not": r["reason"]})
            if sub is None and not hv["to_cash"]:
                harvest_log.append({**entry, "outcome": "no_substitute", "tried": tried})
                continue
            picks.append(R.HarvestPick("long", h.key, h.label, sub["key"] if sub else None,
                                       sub["label"] if sub else None, u, net, term, "long at a loss"))
            rate = None if rates is None else rates["st" if term == "short" else "lt"]
            harvest_log.append({**entry, "outcome": "proposed", "substitute": sub["key"] if sub else None,
                                "substitute_label": sub["label"] if sub else None, "tried": tried,
                                "est_tax_value": None if rate is None else str(R.cents(-net * rate))})

    budget = Decimal(str(link["gain_budget"])) if link.get("gain_budget") is not None else None
    ytd = await RP.realized_ytd(conn, org_id, account["id"], today)
    from services import lot_method as LM

    method, eff = await LM.lot_method_for(conn, org_id, account, today, None)
    hold_rules = {h.key: r for h in hold["holdings"]
                  if (r := WS.sale_restriction(h.key, account_class=WS.tax_class(account), ctx=ctx))
                  and h.key not in {p.key for p in picks}}
    bs = R.BookSettings(max_short_per_name=Decimal(str(st["max_short_per_name"])), exclusions=excl,
                        ceiling_bps=ceiling, fractional=available(frac), min_trade=min_trade,
                        sale=E.SaleSettings(today=today, gain_budget=budget, realized_ytd=ytd,
                                            avoid_short_term=link["avoid_short_term"],
                                            short_term_wait_days=link["short_term_wait_days"],
                                            fractional=available(frac), wash_hold=hold_rules, method=method),
                        trading_cost_bps=hv["trading_cost_bps"])
    res = R.build_books(hold["holdings"], list(shorts.values()), hold["cash"], target, labels, prices, borrow, bs,
                        blocked_1259=blocks, short_washes=washes, harvest=picks)
    warnings += res.warnings
    # rep6 household wash-sale control on the long book's buys (rule a): a buy inside a household loss window is
    # deferred (recorded, never routed) — no substitution is chosen here beyond the harvest's own
    acls = WS.tax_class(account)
    own = [WS.Event(t.key, "sell", today, today, True, str(account["id"]), account["account_number_masked"], acls,
                    "this_proposal") for t in res.trades if t.side == "sell" and (t.est_st_gain + t.est_lt_gain) < 0]
    for i, t in enumerate(res.trades):
        if t.side == "buy":
            r = WS.evaluate_buy(t.key, account_class=acls, ctx=ctx, own=own)
            if r["outcome"] != "clear":
                r = {**r, "outcome": "blocked" if r["outcome"] == "blocked" else "deferred"}
                r.pop("replacement", None)
            res.flags[i] = r
    for i, t in enumerate(res.trades):
        if t.side == "sell_short" and res.flags.get(i, {}).get("rule") == "1259":
            warnings.append({"code": "constructive_sale", "message": res.flags[i]["reason"], "key": t.key})
        if t.side == "sell_short" and res.flags.get(i, {}).get("rule") == "short_wash":
            warnings.append({"code": "short_wash", "message": res.flags[i]["reason"], "key": t.key})
        if t.side == "sell_short" and "borrow_rate_needed" in res.legs[i]["flags"]:
            warnings.append({"code": "borrow_rate_needed", "key": t.key,
                             "message": f"{t.label}: no borrow rate on record — enter one (Long/short → Borrow "
                                        "rates) before this proposal can be approved"})
    # the post-trade short book, for the comparison
    short_after: dict[str, tuple[Decimal, Decimal]] = {k: (x.quantity, x.price) for k, x in shorts.items()
                                                       if x.price is not None}
    traded = Decimal(0)
    for i, t in enumerate(res.trades):
        if res.flags.get(i, {}).get("outcome") not in ("clear", "replaced"):
            continue
        v = t.quantity * t.price if t.quantity is not None and t.price is not None else (t.notional or Decimal(0))
        traded += v
        if t.side == "sell_short":
            q0 = short_after.get(t.key, (Decimal(0), t.price))[0]
            short_after[t.key] = (q0 + t.quantity, t.price)
        elif t.side == "buy_to_cover":
            q0, p0 = short_after.get(t.key, (Decimal(0), t.price))
            short_after[t.key] = (q0 - t.quantity, p0)
    short_after = {k: v for k, v in short_after.items() if v[0] > 0}
    for i, t in enumerate(res.trades):        # a line flagged 'borrow rate needed' still counts in the book
        if t.side == "sell_short" and res.flags.get(i, {}).get("outcome") == "clear" and t.key not in short_after:
            short_after[t.key] = (t.quantity, t.price)
    bb = R.short_book_borrow(short_after, borrow)
    overlay = await RP._setting(conn, org_id, OVERLAY_BPS_SETTING, None, lo=Decimal(0), hi=Decimal(10000))
    comparison = R.cost_comparison(traded_value=traded, trading_cost_bps=hv["trading_cost_bps"], borrow=bb,
                                   overlay_bps=overlay)
    # tracking error vs the model (signed), when every name has a series (prices1); never invented
    te = await tracking_error(conn, org_id, res.after, res.model_target, today)
    blocking = list(res.blocking)
    limit = Decimal(str(st["tracking_error_limit"]))
    te["limit"] = str(limit)
    if te.get("estimated"):
        te["over_limit"] = Decimal(te["tracking_error"]) > limit
        if te["over_limit"]:
            blocking.append("tracking_error_over_limit")
            warnings.append({"code": "tracking_error_over_limit", "message": f"estimated tracking error "
                             f"{te['text']} is above the account's limit {R.pct_text(limit)}"})
    else:
        te["over_limit"] = None
        warnings.append({"code": "tracking_error_needs_prices", "message": f"tracking error: {te['text']}"})
    m_init = await capability(conn, key, "margin_initial_pct")
    m_maint = await capability(conn, key, "margin_maintenance_pct")
    margin = R.margin_text(m_init.value, m_maint.value, res.short_value_after)
    harvested = [x for x in harvest_log if x["outcome"] == "proposed"]
    if rates is None:
        est_tax = {"estimated": False, "text": "not estimated — no tax profile"}
        tax_sentence = "The tax benefit is not estimated (no approved tax profile)."
    else:
        val = sum((Decimal(x["est_tax_value"]) for x in harvested if x.get("est_tax_value")), Decimal(0))
        est_tax = {"estimated": True, "label": "estimate", "harvested_losses": str(R.cents(
                   sum((Decimal(x["net_loss"]) for x in harvested), Decimal(0)))), "est_tax_value": str(R.cents(val)),
                   "rates": {k: str(v) for k, v in rates.items() if isinstance(v, Decimal)},
                   "text": f"${R.cents(val)} (estimate) from harvesting both books in this proposal — a deferral, "
                           "not a permanent saving", "short_rule": "short positions' gains and losses are "
                                                                   "short-term (IRC §1233)"}
        tax_sentence = f"Estimated tax benefit from harvesting in this proposal: ${R.cents(val)} (estimate)."
    lib = await library_risks(conn, org_id, account["id"])      # DISCLOSURES1 — long_short.risks in force
    disc = R.disclosures(borrow_total=bb["borrow_cost_annual"], rebate_total=bb["rebate_annual"], margin=margin,
                         gross_long=Decimal(ls["gross_long"]), gross_short=Decimal(ls["gross_short"]),
                         risks=[(x["key"], x["text"]) for x in lib["items"]])
    disc["library"] = {k: lib.get(k) for k in ("key", "source", "why", "scope", "version_id", "version_no",
                                               "content_sha256", "title", "file_href", "effective_from")}
    summary = R.member_summary(account=account["account_number_masked"], gross_long=Decimal(ls["gross_long"]),
                               gross_short=Decimal(ls["gross_short"]),
                               shorts=sum(1 for t in res.trades if t.side in ("sell_short", "buy_to_cover")),
                               longs=sum(1 for t in res.trades if t.side in ("buy", "sell")),
                               borrow_total=bb["borrow_cost_annual"], est_tax=tax_sentence)
    lines = [(t, res.flags.get(i) or {"outcome": "clear", "rule": None, "security_id": t.key, "label": t.label,
                                      "matches": [], "reason": "clear"}) for i, t in enumerate(res.trades)]
    later_rows = res.later
    br = E.BuildResult(account_value=res.capital, cash=res.cash_before, target=res.target, before=res.before,
                       after=res.after, drift_before=res.drift_before, drift_after=res.drift_after, trades=res.trades,
                       later=later_rows, warnings=res.warnings, notes=[], plan=[], est_st_gain=res.est_st_gain,
                       est_lt_gain=res.est_lt_gain, total_embedded_gain=Decimal(0), years_to_complete=0,
                       plan_by_year=[])
    if not res.trades:
        warnings.append({"code": "no_trades", "message": "both books are at their targets within the rules"})
    thr, thr_src = await RP.drift_threshold(conn, org_id, link, version_id)
    details = {"settings_id": str(st["id"]), "extension": st["extension"], "gross_long": ls["gross_long"],
               "gross_short": ls["gross_short"], "capital": R.cents(res.capital),
               "long_value_after": res.long_value_after, "short_value_after": res.short_value_after,
               "eligibility": el, "borrow": {"lines": res.borrow_lines, **{k: (str(v) if isinstance(v, Decimal) else v)
                                                                            for k, v in bb.items()}},
               "cost_comparison": comparison, "tracking_error": te,
               "constructive_sale": [{"security_id": k, "label": labels.get(k, k), "hits": v,
                                      "text": R.constructive_sale_text(labels.get(k, k), v)}
                                     for k, v in blocks.items() if k in candidates],
               "constructive_sale_read": read,
               "harvest": harvest_log, "skipped": res.skipped, "disclosures": disc, "margin": margin,
               "tax_estimate": est_tax, "member_summary": summary, "blocking": sorted(set(blocking)),
               "model_id": str(model["id"]), "model_version_id": str(version_id),
               "signal": {"source": ls.get("signal_source"), "as_of": _iso(ls.get("signal_as_of"))}}
    return {"result": br, "lines": lines, "warnings": warnings, "labels": labels, "holdings": hold,
            "realized_ytd": ytd, "gain_budget": budget, "fractional": available(frac), "threshold": thr,
            "threshold_source": thr_src, "min_trade": min_trade, "today": today, "lot_method": eff,
            "short_legs": dict(res.legs), "long_short": details, "holdings_account_id": str(account["id"])}


async def tracking_error(conn, org_id, final: dict, model: dict, today: date) -> dict:
    from services import rebalance_proposals as RP
    from services import tilts as TL

    min_obs = await RP._setting(conn, org_id, TE_MIN_OBS_SETTING, DEFAULT_TE_MIN_OBS, lo=10, hi=2520, integer=True)
    window = await RP._setting(conn, org_id, TL.TE_WINDOW_SETTING, TL.DEFAULT_TE_WINDOW, lo=30, hi=3660, integer=True)
    keys = sorted(k for k in set(final) | set(model) if k != E.CASH)
    reader = history_reader or TL.history_for
    hist, prov = await reader(conn, org_id, keys, today, window)
    out = R.te_pure({k: Decimal(v) for k, v in final.items()}, {k: Decimal(v) for k, v in model.items()}, hist,
                    min_obs=min_obs)
    out["window_days"] = window
    out["min_observations"] = min_obs
    out["provenance"] = {k: v for k, v in (prov or {}).items()} if isinstance(prov, dict) else {}
    return out


async def write_details(conn, org_id, proposal_id, c: dict) -> None:
    """Inside rebalance_proposals._write_generated's transaction."""
    d = c["long_short"]
    await conn.execute(
        f"""INSERT INTO {T_LSP} (org_id, proposal_id, account_id, model_id, model_version_id, settings_id, extension,
                gross_long, gross_short, capital, long_value_after, short_value_after, eligibility, borrow,
                cost_comparison, tracking_error, constructive_sale, harvest, skipped, disclosures, margin,
                tax_estimate, member_summary, blocking)
            VALUES ($1::uuid, $2::uuid, $3::uuid, $4::uuid, $5::uuid, $6::uuid, $7, $8, $9, $10, $11, $12, $13::jsonb,
                    $14::jsonb, $15::jsonb, $16::jsonb, $17::jsonb, $18::jsonb, $19::jsonb, $20::jsonb, $21::jsonb,
                    $22::jsonb, $23, $24::jsonb)""",
        str(org_id), str(proposal_id), str(c["holdings_account_id"]), d["model_id"], d["model_version_id"],
        d["settings_id"], d["extension"], Decimal(str(d["gross_long"])), Decimal(str(d["gross_short"])), d["capital"],
        d["long_value_after"], d["short_value_after"], _dumps({**d["eligibility"], "read": d["constructive_sale_read"]}),
        _dumps(d["borrow"]), _dumps(d["cost_comparison"]), _dumps(d["tracking_error"]),
        _dumps(d["constructive_sale"]), _dumps(d["harvest"]), _dumps(d["skipped"]), _dumps(d["disclosures"]),
        _dumps(d["margin"]), _dumps(d["tax_estimate"]), d["member_summary"], _dumps(d["blocking"]))


async def details_of(conn, proposal_id) -> dict | None:
    r = await conn.fetchrow(f"SELECT * FROM {T_LSP} WHERE proposal_id = $1::uuid AND valid_to IS NULL "
                            "AND system_to IS NULL", str(proposal_id))
    return dict(r) if r else None


def details_out(r: dict | None) -> dict | None:
    if not r:
        return None
    out = {k: (_loads(r[k]) if k in ("eligibility", "borrow", "cost_comparison", "tracking_error", "constructive_sale",
                                     "harvest", "skipped", "disclosures", "margin", "tax_estimate", "blocking",
                                     "acknowledgement") else r[k]) for k in r.keys()}
    for k in ("id", "org_id", "proposal_id", "account_id", "model_id", "model_version_id", "settings_id",
              "acknowledged_by"):
        out[k] = str(out[k]) if out.get(k) else None
    for k in ("gross_long", "gross_short", "capital", "long_value_after", "short_value_after"):
        out[k] = _num(out[k])
    for k in ("acknowledged_at", "created_at", "valid_from", "valid_to", "system_from", "system_to"):
        out[k] = _iso(out.get(k))
    out["blocking_labels"] = [{"key": b, "label": R.BLOCKING_LABELS.get(b, b)} for b in out["blocking"] or []]
    out["acknowledgement_items"] = ack_items_of((out.get("disclosures") or {}).get("risks"))
    return out


# ═══ Submit, approval, acknowledgement ═══════════════════════════════════════
async def acknowledge(pool, *, org_id, actor_id, proposal_id, items: list[str]) -> dict:
    """The adviser (the proposal's maker) acknowledges every disclosure — the risks in force (DISCLOSURES1: the
    library's long_short.risks version for the account, else the built-in six), the borrow estimate, the margin
    requirement and the member summary — before submitting. rep13's record on the proposal is written once; the
    library acknowledgement names the VERSION (id + hash). When a newer version is published, acknowledging again
    records it (the proposal's own record stays as first written)."""
    from services import rebalance_proposals as RP

    async with pool.acquire() as conn:
        actor = await HT.assert_human(conn, actor_id, org_id)
        p = await RP.load(conn, org_id, proposal_id)
        if p["maker_kind"] != "user" or str(p["maker_id"]) != str(actor["id"]):
            raise MakerCheckerViolation("the disclosures are acknowledged by the adviser who made the proposal")
        if p["status"] != "draft":
            raise LongShortConflict("only a draft is acknowledged (before it is submitted)")
        state = await disclosure_state(conn, org_id, p, actor["id"])
        want = [x["key"] for x in state["items"]]
        labels = {x["key"]: x["label"] for x in state["items"]}
        missing = [k for k in want if k not in set(items or [])]
        extra = sorted(set(items or []) - set(want))
        if extra:
            raise LongShortError(f"not a disclosure: {extra}")
        if missing:
            raise LongShortError("acknowledge every disclosure: missing " + ", ".join(labels[k] for k in missing))
        async with conn.transaction():
            d = await conn.fetchrow(f"SELECT * FROM {T_LSP} WHERE proposal_id = $1::uuid AND valid_to IS NULL "
                                    "AND system_to IS NULL FOR UPDATE", str(p["id"]))
            if d is None:
                raise LongShortNotFound("not a long/short proposal")
            res = state["resolved"]
            if state["required"] and not state["current"]:
                await DL.acknowledge(conn, org_id=org_id, user_id=actor["id"], resolved=res, subject=_subject(p),
                                     screen=ACK_SCREEN)
            if d["acknowledged_by"] is not None:
                return dict(d)
            new = await conn.fetchrow(
                f"UPDATE {T_LSP} SET acknowledged_by = $2::uuid, acknowledged_at = now(), acknowledgement = $3::jsonb "
                "WHERE id = $1 RETURNING *", d["id"], str(actor["id"]),
                _dumps({"items": want, "texts": labels, "member_summary": d["member_summary"],
                        "library": {"key": RISKS_KEY, "source": res.get("source"), "version_id": res.get("version_id"),
                                    "content_sha256": res.get("content_sha256")}}))
            await _audit(conn, org_id, actor["id"], "disclosures_acknowledged", "rebalance_proposal", p["id"],
                         {"items": want, "library_version_id": res.get("version_id")})
    return dict(new)


async def presubmit(conn, org_id, p: dict) -> None:
    """rebalance_proposals._submit: a long/short proposal is submitted only after its maker acknowledged — and, when
    the library has a long_short.risks version in force, only if that acknowledgement names THAT version."""
    d = await details_of(conn, p["id"])
    if d is None:
        return
    if d["acknowledged_by"] is None:
        raise LongShortConflict("acknowledge the long/short disclosures (the risks, the borrow-cost estimate, the "
                                "margin requirement and the member summary) before submitting")
    if p["maker_id"] is None or str(d["acknowledged_by"]) != str(p["maker_id"]):
        raise LongShortConflict("the disclosures were not acknowledged by the proposal's maker")
    why = await _library_refusal(conn, org_id, p)
    if why:
        raise LongShortConflict(why)


async def _library_refusal(conn, org_id, p: dict) -> str | None:
    st = await disclosure_state(conn, org_id, p)
    if not st["required"] or st["current"]:
        return None
    res = st["resolved"]
    if st["acknowledged"]:
        return (f"a newer version of '{res['title']}' (version {res['version_no']}) was published since the "
                "acknowledgement — the maker must read it and acknowledge again")
    return f"acknowledge '{res['title']}' (version {res['version_no']}) before submitting"


async def approval_refusal(conn, org_id, p: dict, today: date | None = None) -> str | None:
    """Why a long/short proposal cannot be approved NOW, or None. Re-reads eligibility (any lapsed piece refuses)
    and the household (§1259) as of today."""
    from services import rebalance_proposals as RP

    d = await details_of(conn, p["id"])
    if d is None:
        return None
    today = today or date.today()
    if d["acknowledged_by"] is None:
        return "the disclosures were never acknowledged"
    why = await _library_refusal(conn, org_id, p)          # DISCLOSURES1 — the version in force now
    if why:
        return why
    account = await AM.load_account(conn, org_id, p["account_id"])
    el = await eligibility(conn, org_id, account)
    if not el["eligible"]:
        return ("eligibility lapsed since the proposal was built — " + el["refusal"])
    blocking = _loads(d["blocking"]) or []
    if blocking:
        return "refused: " + "; ".join(R.BLOCKING_LABELS.get(b, b) for b in blocking)
    trades = await RP.trades_of(conn, p["id"])
    shorts = [str(t["global_security_id"]) for t in trades if t["side"] == "sell_short"
              and WS.routable(_loads(t.get("wash_sale")))]
    rates = await borrow_rates_for(conn, org_id, shorts, today)
    no_rate = [k for k in shorts if k not in rates]
    if no_rate:
        return f"refused: borrow rate needed for {len(no_rate)} short line(s)"
    blocks, _ = await constructive_sale_check(conn, org_id, account, shorts, today)
    if blocks:
        k, hits = next(iter(blocks.items()))
        return R.constructive_sale_text(k, hits)
    return None


async def _approval_precheck(pool, task, outcome, actor_id):
    if outcome != "approve":
        return
    from services import rebalance_proposals as RP

    async with pool.acquire() as conn:
        p = await conn.fetchrow(f"SELECT * FROM {RP.T_P} WHERE approval_task_id = $1::uuid AND system_to IS NULL",
                                task["id"])
        if p is None:
            return
        why = await approval_refusal(conn, p["org_id"], dict(p))
    if why:
        raise HT.HumanTaskConflict(f"long/short approval refused — {why}")


# ═══ Execution ═══════════════════════════════════════════════════════════════
def short_terms_of(t: dict) -> dict | None:
    if t["side"] not in R.POSITION_EFFECT:
        return None
    leg = _loads(t.get("short_leg")) or {}
    out = {"position_effect": R.POSITION_EFFECT[t["side"]], "term": "short",
           "borrow_rate_bps": leg.get("borrow_rate_bps"), "rate_as_of": leg.get("rate_as_of")}
    if t["side"] == SIDE_CLOSE:
        out["lots"] = [{"short_lot_id": x["short_lot_id"], "quantity": x["quantity"]} for x in leg.get("lots") or []]
    return out


async def instruction_guard(conn, org_id, acct: dict, trade) -> None:
    """services.execution.create_instructions — EVERY instruction that opens a short (a proposal's or a manual
    one): eligibility and the §1259 household check, today. A cover only needs an open short to close."""
    if trade.side not in R.POSITION_EFFECT:
        return
    if trade.quantity is None:
        raise LongShortError("a short or a cover is a number of shares, never an amount")
    if trade.side == SIDE_OPEN:
        await require_eligible(conn, org_id, acct, "a SELL SHORT instruction")
        blocks, _ = await constructive_sale_check(conn, org_id, acct, [str(trade.global_security_id)], date.today())
        if blocks:
            hits = blocks[str(trade.global_security_id)]
            raise ConstructiveSaleBlocked(R.constructive_sale_text(hits[0]["label"], hits), hits)
        return
    book, _ = await short_book(conn, org_id, [str(acct["id"])])
    pos = book.get(str(acct["id"]), {}).get(str(trade.global_security_id))
    pending = await conn.fetchval(
        "SELECT COALESCE(sum(i.quantity), 0) FROM public.execution_instructions i WHERE i.account_id = $1::uuid "
        "AND i.global_security_id = $2::uuid AND i.side = 'buy_to_cover' AND i.system_to IS NULL "
        "AND i.status = ANY($3::text[])", str(acct["id"]), str(trade.global_security_id),
        ["draft", "pending_approval", "approved", "sent", "partially_filled"])
    open_q = pos.quantity if pos else Decimal(0)
    if trade.quantity + Decimal(str(pending)) > open_q:
        raise LongShortConflict(f"BUY TO COVER {trade.quantity} is more than the {open_q} shares short (less "
                                f"{pending} already instructed to cover)")


async def execute(pool, *, org_id, p: dict, actor: dict, route: str = "adapter") -> dict:
    """An APPROVED long/short proposal → instructions. 'adapter': the account's custodian adapter (it refuses a
    short at submit when its custodian does not declare short_selling); 'manual': the manual route — always
    offered: instructions a person carries out with the printed order sheet (SELL SHORT / BUY TO COVER)."""
    from services import rebalance_proposals as RP

    if route not in ("adapter", "manual"):
        raise LongShortError("route is adapter or manual")
    return await RP.execute(pool, org_id=org_id, proposal_id=p["id"], actor_id=actor["id"], manual=route == "manual")


# ═══ Reads ══════════════════════════════════════════════════════════════════
async def account_panel(conn, org_id, account_id, today: date | None = None) -> dict:
    from services import rebalance_proposals as RP

    today = today or date.today()
    account = await AM.load_account(conn, org_id, account_id)
    el = await eligibility(conn, org_id, account)
    book, realized = await short_book(conn, org_id, [str(account["id"])])
    shorts = book.get(str(account["id"]), {})
    await price_shorts(conn, org_id, shorts, today)
    link = await AM.active_link(conn, account["id"])
    model = await model_kind(conn, link["model_id"]) if link else None
    year = [x for x in realized if x["on"].year == today.year]
    return {"account": {"id": str(account["id"]), "account_number_masked": account["account_number_masked"],
                        "custodian_code": account["custodian_code"]},
            "eligibility": el, "margin_agreement": suitability_out(await margin_agreement(conn, org_id, account)),
            "settings": settings_out(await settings_of(conn, account["id"], "saved")),
            "draft": settings_out(await settings_of(conn, account["id"], "draft")),
            "history": await settings_history(conn, account["id"]),
            "model": None if model is None else {**{k: (str(v) if isinstance(v, (Decimal, date)) else v)
                                                    for k, v in model.items()}, "model_id": str(link["model_id"])},
            "short_book": [{"security_id": k, "label": x.label, "quantity": str(x.quantity), "signed_quantity":
                            str(-x.quantity), "price": _num(x.price), "price_as_of": _iso(x.price_as_of),
                            "unrealized": None if x.unrealized is None else str(R.cents(x.unrealized)),
                            "term": "short", "lots": [{"short_lot_id": lot.id, "quantity": str(lot.quantity),
                                                       "open_price": str(lot.open_price),
                                                       "opened": lot.opened.isoformat()} for lot in x.lots]}
                           for k, x in sorted(shorts.items(), key=lambda kv: kv[1].label)],
            "realized_short_ytd": {"gain": str(R.cents(sum((x["gain"] for x in year), Decimal(0)))), "term": "short",
                                   "closes": len(year), "rule": "IRC §1233 — short-term whatever the holding period"},
            "open_proposal": (lambda op: str(op["id"]) if op else None)(await RP.open_proposal(conn, account["id"]))}


async def tax_estimate(conn, org_id, account_id, today: date | None = None) -> dict:
    """The long/short account's estimated tax position: realized short-term from covers this year, the short
    book's unrealized gain / loss, and the value of its unrealized losses at the profile's ST rate — 'estimate'."""
    from services import tax_profiles as TP

    today = today or date.today()
    panel = await account_panel(conn, org_id, account_id, today)
    account = await AM.load_account(conn, org_id, account_id)
    rates = TP.combined_rates(await TP.profile_for_account(conn, org_id, account, today.year))
    unreal = [Decimal(x["unrealized"]) for x in panel["short_book"] if x["unrealized"] is not None]
    losses = sum((u for u in unreal if u < 0), Decimal(0))
    out = {"label": "estimate", "realized_short_term_ytd": panel["realized_short_ytd"]["gain"],
           "short_book_unrealized": str(R.cents(sum(unreal, Decimal(0)))),
           "harvestable_short_losses": str(R.cents(losses)), "rule": "short positions' gains and losses are "
                                                                      "short-term (IRC §1233)"}
    if rates is None:
        out.update({"estimated": False, "text": "not estimated — no tax profile"})
    else:
        out.update({"estimated": True, "st_rate": str(rates["st"]),
                    "est_tax_benefit": str(R.cents(-losses * rates["st"])),
                    "text": f"${R.cents(-losses * rates['st'])} (estimate) — the short book's unrealized losses at "
                            "the short-term rate; a deferral, not a permanent saving"})
    return out


def vocabularies() -> dict:
    def pairs(d):
        return [{"key": k, "label": v} for k, v in d.items()]

    return {"extensions": pairs(R.EXTENSION_LABELS), "sides": pairs(R.SIDE_LABELS),
            "skip_reasons": pairs(R.SKIP_LABELS), "line_flags": pairs(R.LINE_FLAG_LABELS),
            "blocking": pairs(R.BLOCKING_LABELS), "risks": [{"key": k, "label": t} for k, t in R.RISKS],
            "acknowledgement_items": ack_items_of(None), "risks_disclosure_key": RISKS_KEY,
            "suitability_statuses": pairs(SUIT_STATUS_LABELS),
            "rules": {"short_term": "IRC §1233 — a short sale's gain or loss is short-term whatever the holding period",
                      "constructive_sale": "IRC §1259 — no short of a security (or another share class of the same "
                                           "issuer) any household account holds long at a gain; never overridden",
                      "short_wash": "IRC §1091(e) — no new short of a name the household covered at a loss within 30 "
                                    "days",
                      "borrow": "borrow rates are ENTERED (no live borrow feed); a short without one blocks approval",
                      "custodian": "long/short runs only where the custodian declares margin, short selling and stock "
                                   "borrow — none does today; the manual route is always offered"}}
