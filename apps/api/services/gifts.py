"""REP10 — charitable lot gifting: the highest-gain long-term lots given in kind
to a public charity, a donor-advised fund or a private foundation, the tax
saved shown as an ESTIMATE, delivered MANUALLY (always), with an optional rebuy.

CANDIDATES — :func:`candidates`
──────────────────────────────────────────────────────────────────────────────
Every open lot of the tax subject's accounts (rep6: the household, else the
member), priced from the account's own valuation (the rep5 holding price, its
as-of stated). A lot is a candidate when it is LONG-TERM on today's date
(held more than a year — rebalance_engine.term_of), at a GAIN, with a known
cost, in a managed or advise account (both deliver by the same manual letter).
Ordered by gain per dollar given ((price − cost) ÷ price), highest first; ties
by the larger gain, then the lot id. Excluded, each with its reason:
  short-term   — "short-term: the deduction is limited to basis — not offered"
  at a loss    — "at a loss: sell it, take the loss, and give cash instead"
  read_only    — "read-only account — never traded"
  unpriced / unknown cost / unknown term — "not estimated — needs …"
  average cost — "carried at average cost — no lot choice for an in-kind gift"
The remaining position keeps its own lots and the account's lot method (a gift
names specific lots; the method still governs every later sale).

THE COMPARISON — :func:`compare` (pure, every figure an estimate with its rates)
──────────────────────────────────────────────────────────────────────────────
FMV = Σ quantity × price;  basis = Σ quantity × unit cost;  gain = FMV − basis.
Gift in kind:
  capital-gains tax avoided = gain × the profile's LONG-TERM combined rate
    (federal LT + NIIT when it applies + state + city — rep6's combined_rates);
  deduction this year = min(FMV, in-kind limit × AGI), the excess carried
    forward (up to five years — not valued here); value = that × the
    profile's FEDERAL ordinary rate. Only when the member itemizes; otherwise
    "no deduction value — does not itemize". No AGI estimate → "not
    estimated — needs an AGI estimate".
Sell and give the same FMV in cash (the alternative):
  capital-gains tax paid = the same gain × LT rate; deduction this year =
  min(FMV, cash limit × AGI), carryforward the rest; value at the ordinary rate.
Tax saved by giving in kind = tax avoided + (in-kind deduction value − cash
deduction value). AGI limits (estimates; the CPA confirms): public charity
and DAF 30% (appreciated long-term stock) / 60% (cash); private foundation
20% / 30% (FMV only for publicly traded "qualified appreciated stock").
The prior carryforward (entered, with its source and as-of) is used AFTER this
year's gifts within the same limit — shown, not added to this gift's value.

DELIVERY — manual: approve (a second person) → print the transfer letter
(services.gift_letter; on demand, never stored) → sent → RECEIVED (the date
the donee's account received the shares = the gift date, entered by a
person) → acknowledged (the donee's receipt noted). Form 8283 above $500 and
the year-end lead time are reminders (a to-do from the scheduler sweep when a
current-year gift is not received by the firm's date).
REBUY — none | same security (a fresh basis at the rebuy price; no wash-sale
issue on a gain — stated) | replica-consistent (the account's normal rep5
proposal). Either is a rep5 proposal a person approves.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_CEILING, Decimal, localcontext

from services import human_tasks as HT
from services import rebalance_engine as E
from services import rep10_common as C

log = logging.getLogger(__name__)

T_DONEE = "public.charitable_donees"
T_GIFT = "public.gift_proposals"
T_LOT = "public.gift_lots"
VIEW = "view_tax"
MANAGE = "manage_tax"
APPROVE = "approve_tax"
TASK_KIND = "gift_proposal_approval"
SUBJECT = "gift_proposal"
TODO_YEAR_END = "gift_year_end"
YEAR_END_SETTING = "replication.gifts.year_end_receive_by"
DEFAULT_YEAR_END = "12-15"
FORM_8283_THRESHOLD = Decimal("500")

DONEE_KIND_LABELS = {"public_charity": "Public charity", "daf": "Donor-advised fund",
                     "private_foundation": "Private foundation"}
# (appreciated long-term stock, cash) — fractions of AGI. Estimates; the firm's CPA confirms.
AGI_LIMITS = {"public_charity": (Decimal("0.30"), Decimal("0.60")), "daf": (Decimal("0.30"), Decimal("0.60")),
              "private_foundation": (Decimal("0.20"), Decimal("0.30"))}
CARRYFORWARD_YEARS = 5
STATUS_LABELS = {"draft": "Draft", "pending_approval": "Awaiting approval", "approved": "Approved — print the letter",
                 "sent": "Letter sent", "received": "Received by the donee", "acknowledged": "Acknowledged",
                 "cancelled": "Cancelled"}
REBUY_LABELS = {"none": "No rebuy", "same_security": "Rebuy the same security (fresh basis)",
                "replica_consistent": "Rebuy to the replica (the account's normal proposal)"}
EXCLUSION_LABELS = {
    "short_term": "short-term: the deduction is limited to basis — not offered",
    "loss": "at a loss: sell it, take the loss, and give cash instead",
    "read_only": "read-only account — never traded",
    "unpriced": "not estimated — needs prices",
    "unknown_cost": "not estimated — needs the lot's cost",
    "unknown_term": "not estimated — needs the lot's acquisition date",
    "average_cost": "carried at average cost — no lot choice for an in-kind gift",
}
OPEN_ITEMS = (
    "AGI limits (30% of AGI for appreciated long-term stock to a public charity or DAF, 20% to a private foundation; "
    "60% / 30% for cash) and the five-year carryover are shown as estimates — the firm's CPA confirms each gift.",
    "Whether the member itemizes is entered on each proposal with its source and as-of — the CPA confirms.",
    "Form 8283 is needed for non-cash gifts over $500 (Section B rules above $5,000) — the CPA confirms.",
    "The donee must accept in-kind securities — confirm with the donee before the letter is sent.",
    "State deductibility of a charitable gift varies — the deduction value uses the federal ordinary rate only.",
)
STATE_NOTE = "deduction value at the federal ordinary rate only (state treatment varies — CPA)"
NO_ITEMIZE = "no deduction value — does not itemize"
NO_AGI = "not estimated — needs an AGI estimate"
NO_PROFILE = "not estimated — no tax profile"
REBUY_NOTE = ("A rebuy right after an in-kind gift of a GAIN lot has no wash-sale issue (the rule is about losses); "
              "the rebought shares start a fresh basis at the rebuy price and a new holding period.")


# ═══ Pure ═══════════════════════════════════════════════════════════════════
def gain_per_dollar(price: Decimal, unit_cost: Decimal) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = 50
        return ((price - unit_cost) / price).quantize(C.Q)


def classify(lot: E.Lot, price: Decimal | None, today: date, *, authority: str, average: bool) -> str | None:
    """None = a candidate; else the exclusion code."""
    if authority not in ("managed", "advise"):
        return "read_only"
    if average:
        return "average_cost"
    if price is None or price <= 0:
        return "unpriced"
    if lot.unit_cost is None:
        return "unknown_cost"
    term = E.term_of(lot, today)
    if term == "unknown" or lot.acquired is None:      # the gift lot records its acquisition date (Form 8283)
        return "unknown_term"
    if price <= lot.unit_cost:
        return "loss"
    if term != "long":
        return "short_term"
    return None


def order_key(c: dict):
    return (-Decimal(c["gain_per_dollar"]), -Decimal(c["gain"]), c["tax_lot_id"])


def pick_for_amount(cands: list[dict], target: Decimal) -> list[dict]:
    """Candidates in order until the FMV reaches ``target``; the last lot only as far as needed, rounded UP to a
    whole share (capped at the lot; a lot of less than one share is taken whole)."""
    out, acc = [], C.ZERO
    for c in sorted(cands, key=order_key):
        if acc >= target:
            break
        q, px = Decimal(c["quantity"]), Decimal(c["price"])
        need = target - acc
        if q * px > need:
            q2 = (need / px).to_integral_value(rounding=ROUND_CEILING)
            q = min(q, q2) if q >= 1 else q
        out.append({**c, "quantity": str(q)})
        acc += q * px
    return out


def compare(lots: list[dict], *, rates: dict | None, donee_kind: str, itemizes: str, agi: Decimal | None,
            prior_carryforward: Decimal) -> dict:
    """``lots`` [{quantity, price, unit_cost}] → the in-kind vs sell-and-give-cash comparison. Exact; money at the
    end of each line (ROUND_HALF_EVEN, cents)."""
    with localcontext() as ctx:
        ctx.prec = 50
        fmv = sum((Decimal(x["quantity"]) * Decimal(x["price"]) for x in lots), C.ZERO)
        basis = sum((Decimal(x["quantity"]) * Decimal(x["unit_cost"]) for x in lots), C.ZERO)
    fmv, basis = C.money(fmv), C.money(basis)
    gain = fmv - basis
    kind_lim, cash_lim = AGI_LIMITS[donee_kind]
    out = {"label": C.ESTIMATE, "fmv": str(fmv), "basis": str(basis), "gain": str(gain), "donee_kind": donee_kind,
           "limits": {"in_kind": str(kind_lim), "cash": str(cash_lim), "carryforward_years": CARRYFORWARD_YEARS},
           "itemizes": itemizes, "agi_estimate": str(agi) if agi is not None else None,
           "prior_carryforward": str(prior_carryforward), "notes": [STATE_NOTE]}
    if rates is None:
        out["in_kind"] = {"cg_tax_avoided": None, "cg_note": NO_PROFILE}
        out["cash"] = {"cg_tax_paid": None, "cg_note": NO_PROFILE}
        out["tax_saved_in_kind"] = None
        out["tax_saved_note"] = NO_PROFILE
        out["rates"] = {"estimated": False, "label": NO_PROFILE}
        return out
    lt = Decimal(str(rates["lt"]))
    ordinary = Decimal(str(rates["parts"]["federal_ordinary"]))
    out["rates"] = {"estimated": True, "lt_combined": str(lt), "federal_ordinary": str(ordinary),
                    "as_of": rates.get("as_of"), "tax_year": rates.get("tax_year"), "parts": rates.get("parts")}
    cg = C.money(gain * lt)
    ik = {"cg_tax_avoided": str(cg), "cg_note": f"gain × the long-term combined rate {lt}"}
    ca = {"cg_tax_paid": str(cg), "cg_note": f"gain × the long-term combined rate {lt}"}

    def deduction(limit_pct: Decimal) -> dict:
        limit = C.money(agi * limit_pct)
        this_year = min(fmv, limit)
        carry = fmv - this_year
        room = limit - this_year
        prior_used = min(prior_carryforward, room)
        return {"limit": str(limit), "deductible_this_year": str(this_year), "carryforward": str(carry),
                "prior_carryforward_usable": str(prior_used),
                "value": str(C.money(this_year * ordinary)),
                "note": f"min(FMV, {limit_pct} × AGI) × the federal ordinary rate {ordinary}; the excess carries "
                        f"forward up to {CARRYFORWARD_YEARS} years (not valued here)"}

    if itemizes != "yes":
        ik["deduction"] = {"value": None, "note": NO_ITEMIZE}
        ca["deduction"] = {"value": None, "note": NO_ITEMIZE}
        saved = cg
        note = "capital-gains tax avoided (no deduction value either way — does not itemize)"
    elif agi is None:
        ik["deduction"] = {"value": None, "note": NO_AGI}
        ca["deduction"] = {"value": None, "note": NO_AGI}
        saved = cg
        note = "capital-gains tax avoided only — the deduction is " + NO_AGI
    else:
        ik["deduction"] = deduction(kind_lim)
        ca["deduction"] = deduction(cash_lim)
        saved = cg + Decimal(ik["deduction"]["value"]) - Decimal(ca["deduction"]["value"])
        note = "capital-gains tax avoided + (in-kind deduction value − cash deduction value)"
    out["in_kind"], out["cash"] = ik, ca
    out["tax_saved_in_kind"] = str(saved)
    out["tax_saved_note"] = note
    return out


def reminders(fmv: Decimal, *, tax_year: int, receive_by: date | None) -> list[dict]:
    out = []
    if fmv > FORM_8283_THRESHOLD:
        out.append({"code": "form_8283", "message": "Form 8283 is needed with the member's return for a non-cash "
                    f"gift over ${FORM_8283_THRESHOLD} (Section B rules above $5,000) — the CPA confirms"})
    if receive_by is not None:
        out.append({"code": "year_end", "message": f"for a {tax_year} deduction the shares must reach the donee's "
                    f"account by 31 December — the firm's lead-time date is {receive_by.isoformat()}"})
    return out


# ═══ Donees ═════════════════════════════════════════════════════════════════
def donee_out(r: dict) -> dict:
    return {"id": str(r["id"]), "name": r["name"], "kind": r["kind"], "kind_label": DONEE_KIND_LABELS[r["kind"]],
            "ein": r["ein"], "receiving_broker": r["receiving_broker"], "dtc_participant": r["dtc_participant"],
            "account_number_masked": r["account_number_masked"], "contact": r["contact"], "notes": r["notes"],
            "status": r["status"], "created_at": C.iso(r["created_at"])}


async def list_donees(conn, org_id) -> list[dict]:
    return [donee_out(dict(r)) for r in await conn.fetch(
        f"SELECT * FROM {T_DONEE} WHERE org_id = $1::uuid AND system_to IS NULL ORDER BY status, name", str(org_id))]


async def _donee(conn, org_id, donee_id) -> dict:
    row = await conn.fetchrow(f"SELECT * FROM {T_DONEE} WHERE id = $1::uuid AND org_id = $2::uuid AND system_to IS NULL",
                              C.uid(donee_id, "donee"), str(org_id))
    if row is None:
        raise C.Rep10NotFound("donee not found")
    return dict(row)


async def create_donee(pool, *, org_id, actor_id, name: str, kind: str, ein: str, receiving_broker: str,
                       dtc_participant: str, account_number: str, contact: str | None = None,
                       notes: str | None = None) -> dict:
    """The donee's account number is used ONCE — to store its mask and a salted hash — and never kept."""
    import re

    from services.custody.base import AccountNumber
    from services.custody.registry import get_or_create_salt

    if kind not in DONEE_KIND_LABELS:
        raise C.Rep10Error(f"donee kind is one of {list(DONEE_KIND_LABELS)}")
    if not re.fullmatch(r"[0-9]{2}-[0-9]{7}", (ein or "").strip()):
        raise C.Rep10Error("the EIN is written NN-NNNNNNN")
    if not re.fullmatch(r"[0-9]{4}", (dtc_participant or "").strip()):
        raise C.Rep10Error("the DTC participant number is four digits")
    if not (name or "").strip() or not (receiving_broker or "").strip():
        raise C.Rep10Error("name the donee and its receiving broker")
    try:
        n = AccountNumber(account_number)
    except ValueError as exc:
        raise C.Rep10Error("give the donee's account number at the receiving broker (stored masked)") from exc
    async with pool.acquire() as conn:
        actor = await C.human(conn, actor_id, org_id)
        async with conn.transaction():
            salt = await get_or_create_salt(conn, org_id)
            row = await conn.fetchrow(
                f"INSERT INTO {T_DONEE} (org_id, name, kind, ein, receiving_broker, dtc_participant, "
                "account_number_masked, account_number_hash, contact, notes, created_by) VALUES ($1::uuid, $2, $3, $4, "
                "$5, $6, $7, $8, $9, $10, $11::uuid) RETURNING *",
                str(org_id), name.strip(), kind, ein.strip(), receiving_broker.strip(), dtc_participant.strip(),
                n.masked, n.hashed(salt), (contact or "").strip() or None, (notes or "").strip() or None,
                str(actor["id"]))
            await C.audit(conn, org_id, actor["id"], "donee_created", "charitable_donee", row["id"],
                          {"name": row["name"], "kind": kind, "account_number_masked": n.masked})
        return donee_out(dict(row))


async def retire_donee(pool, *, org_id, actor_id, donee_id) -> dict:
    async with pool.acquire() as conn:
        actor = await C.human(conn, actor_id, org_id)
        d = await _donee(conn, org_id, donee_id)
        if d["status"] == "retired":
            raise C.Rep10Conflict("the donee is already retired")
        async with conn.transaction():
            row = await C.update(conn, T_DONEE, d["id"], {"status": "retired"})
            await C.audit(conn, org_id, actor["id"], "donee_retired", "charitable_donee", d["id"], {})
        return donee_out(row)


# ═══ Candidates ═════════════════════════════════════════════════════════════
async def _subject_accounts(conn, org_id, subject_type: str, subject_id) -> list[dict]:
    from services import account_models as AM

    if subject_type == "household":
        rows = await conn.fetch("SELECT id FROM public.accounts WHERE org_id = $1::uuid AND household_id = $2::uuid "
                                "AND system_to IS NULL AND valid_to IS NULL AND closed_on IS NULL "
                                "ORDER BY account_number_masked", str(org_id), str(subject_id))
    else:
        rows = await conn.fetch("SELECT id FROM public.accounts WHERE org_id = $1::uuid AND household_id IS NULL "
                                "AND primary_entity_id = $2::uuid AND system_to IS NULL AND valid_to IS NULL "
                                "AND closed_on IS NULL ORDER BY account_number_masked", str(org_id), str(subject_id))
    return [await AM.load_account(conn, org_id, r["id"]) for r in rows]


async def candidates(conn, org_id, subject_type: str, subject_id, *, today: date | None = None) -> dict:
    from services import account_models as AM
    from services import lot_method as LM
    from services import tax_profiles as TP

    today = today or date.today()
    subj = await TP.check_subject(conn, org_id, subject_type, subject_id)
    cands, excluded = [], []
    for a in await _subject_accounts(conn, org_id, subject_type, subj["subject_id"]):
        auth = a.get("authority") or "read_only"
        hold = await AM.load_holdings(conn, org_id, a["id"])
        method, _eff = await LM.lot_method_for(conn, org_id, a, today)
        for h in hold["holdings"]:
            for lot in h.lots:
                code = classify(lot, h.price, today, authority=auth, average=h.key in method.average_cost)
                base = {"account_id": str(a["id"]), "account": a["account_number_masked"], "authority": auth,
                        "global_security_id": h.key, "label": h.label, "tax_lot_id": str(lot.id),
                        "quantity": str(lot.quantity), "acquired_date": C.iso(lot.acquired),
                        "unit_cost": str(lot.unit_cost) if lot.unit_cost is not None else None,
                        "price": str(h.price) if h.price is not None else None, "price_as_of": C.iso(h.price_as_of),
                        "price_source": h.price_source, "term": E.term_of(lot, today)}
                if code:
                    excluded.append({**base, "reason_code": code, "reason": EXCLUSION_LABELS[code]})
                    continue
                gain = (h.price - lot.unit_cost) * lot.quantity
                cands.append({**base, "fmv": str(C.money(h.price * lot.quantity)), "gain": str(C.money(gain)),
                              "gain_per_dollar": str(gain_per_dollar(h.price, lot.unit_cost))})
    cands.sort(key=order_key)
    rates = None
    year = today.year
    prof = await TP.approved_profile(conn, org_id, subj["subject_type"], subj["subject_id"], year)
    if prof:
        rates = TP.combined_rates(prof)
    return {"subject": subj, "as_of": today.isoformat(), "candidates": cands, "excluded": excluded,
            "rates": TP.rates_out(rates), "order": "gain per dollar given, highest first",
            "label": C.ESTIMATE}


# ═══ Proposals ══════════════════════════════════════════════════════════════
def gift_out(r: dict, lots: list[dict] | None = None, donee: dict | None = None) -> dict:
    return {"id": str(r["id"]), "household_id": str(r["household_id"]) if r["household_id"] else None,
            "subject_type": r["subject_type"], "subject_id": str(r["subject_id"]),
            "member_entity_id": str(r["member_entity_id"]), "donee_id": str(r["donee_id"]),
            "donee_kind": r["donee_kind"], "tax_year": r["tax_year"],
            "target_amount": str(r["target_amount"]) if r["target_amount"] is not None else None,
            "inputs": {"agi_estimate": str(r["agi_estimate"]) if r["agi_estimate"] is not None else None,
                       "agi_source": r["agi_source"], "agi_as_of": C.iso(r["agi_as_of"]),
                       "itemizes": r["itemizes"], "itemizes_source": r["itemizes_source"],
                       "itemizes_as_of": C.iso(r["itemizes_as_of"]),
                       "prior_carryforward": str(r["prior_carryforward"]),
                       "prior_carryforward_source": r["prior_carryforward_source"],
                       "prior_carryforward_as_of": C.iso(r["prior_carryforward_as_of"])},
            "rebuy": r["rebuy"], "comparison": C.loads(r["comparison"]), "rates": C.loads(r["rates"]),
            "status": r["status"], "maker_id": str(r["maker_id"]),
            "submitted_by": str(r["submitted_by"]) if r["submitted_by"] else None,
            "approved_by": str(r["approved_by"]) if r["approved_by"] else None,
            "approved_at": C.iso(r["approved_at"]), "decision_note": r["decision_note"],
            "sent_at": C.iso(r["sent_at"]), "sent_channel": r["sent_channel"],
            "received_on": C.iso(r["received_on"]), "gift_date": C.iso(r["received_on"]),
            "acknowledged_on": C.iso(r["acknowledged_on"]), "acknowledgement_ref": r["acknowledgement_ref"],
            "rebuy_proposal_ids": [str(x) for x in (r["rebuy_proposal_ids"] or [])],
            "cancel_reason": r["cancel_reason"], "notes": r["notes"], "created_at": C.iso(r["created_at"]),
            "donee": donee, "lots": [lot_out(x) for x in (lots or [])]}


def lot_out(x: dict) -> dict:
    return {"id": str(x["id"]), "account_id": str(x["account_id"]), "tax_lot_id": str(x["tax_lot_id"]),
            "global_security_id": str(x["global_security_id"]), "quantity": str(x["quantity"]),
            "unit_cost": str(x["unit_cost"]), "acquired_date": C.iso(x["acquired_date"]),
            "holding_period": x["holding_period"], "price_at_proposal": str(x["price_at_proposal"]),
            "price_as_of": C.iso(x["price_as_of"]), "price_source": x["price_source"],
            "fmv_at_proposal": str(x["fmv_at_proposal"]), "gain_at_proposal": str(x["gain_at_proposal"]),
            "fmv_at_receipt": str(x["fmv_at_receipt"]) if x["fmv_at_receipt"] is not None else None,
            "receipt_price": str(x["receipt_price"]) if x["receipt_price"] is not None else None,
            "receipt_price_source": x["receipt_price_source"],
            "fmv_at_receipt_note": None if x["fmv_at_receipt"] is not None else C.NEEDS_PRICES}


async def load(conn, org_id, gift_id) -> dict:
    row = await conn.fetchrow(f"SELECT * FROM {T_GIFT} WHERE id = $1::uuid AND org_id = $2::uuid AND system_to IS NULL",
                              C.uid(gift_id, "gift proposal"), str(org_id))
    if row is None:
        raise C.Rep10NotFound("gift proposal not found")
    return dict(row)


async def lots_of(conn, gift_id) -> list[dict]:
    return [dict(r) for r in await conn.fetch(f"SELECT * FROM {T_LOT} WHERE gift_proposal_id = $1::uuid AND valid_to "
                                              "IS NULL AND system_to IS NULL ORDER BY account_id, acquired_date, "
                                              "tax_lot_id", str(gift_id))]


async def detail(conn, org_id, gift_id, *, today: date | None = None) -> dict:
    today = today or date.today()
    g = await load(conn, org_id, gift_id)
    lots = await lots_of(conn, g["id"])
    out = gift_out(g, lots, donee_out(await _donee(conn, org_id, g["donee_id"])))
    fmv = Decimal((out["comparison"] or {}).get("fmv") or "0")
    out["reminders"] = reminders(fmv, tax_year=g["tax_year"], receive_by=await receive_by(conn, org_id, g["tax_year"]))
    return out


async def list_gifts(conn, org_id, *, status: str | None = None) -> list[dict]:
    rows = await conn.fetch(f"SELECT * FROM {T_GIFT} WHERE org_id = $1::uuid AND system_to IS NULL "
                            "AND ($2::text IS NULL OR status = $2::text) ORDER BY created_at DESC LIMIT 200",
                            str(org_id), status)
    return [gift_out(dict(r)) for r in rows]


async def receive_by(conn, org_id, tax_year: int) -> date | None:
    v, _ = await C.setting_text(conn, org_id, YEAR_END_SETTING, DEFAULT_YEAR_END)
    try:
        m, d = (int(x) for x in v.split("-"))
        return date(int(tax_year), m, d)
    except (ValueError, TypeError):
        m, d = (int(x) for x in DEFAULT_YEAR_END.split("-"))
        return date(int(tax_year), m, d)


async def _member_in_subject(conn, org_id, subject: dict, entity_id) -> None:
    eid = C.uid(entity_id, "member")
    if subject["subject_type"] == "entity":
        if eid != subject["subject_id"]:
            raise C.Rep10Error("the member is the tax subject itself")
        return
    ok = await conn.fetchval("SELECT 1 FROM public.household_memberships WHERE household_id = $1::uuid "
                             "AND entity_id = $2::uuid", subject["subject_id"], eid)
    ok = ok or await conn.fetchval("SELECT 1 FROM public.accounts WHERE org_id = $1::uuid AND household_id = $2::uuid "
                                   "AND primary_entity_id = $3::uuid AND system_to IS NULL AND valid_to IS NULL",
                                   str(org_id), subject["subject_id"], eid)
    if not ok:
        raise C.Rep10Error("the member is not in this household")


def _req_text(v, what: str) -> str:
    if not (v or "").strip():
        raise C.Rep10Error(f"{what} needs its source")
    return v.strip()


async def create(pool, *, org_id, actor_id, subject_type: str, subject_id, member_entity_id, donee_id,
                 tax_year: int, target_amount: Decimal | None = None, lots: list[dict] | None = None,
                 agi_estimate: Decimal | None = None, agi_source: str | None = None, agi_as_of: date | None = None,
                 itemizes: str, itemizes_source: str, itemizes_as_of: date,
                 prior_carryforward: Decimal = Decimal(0), prior_carryforward_source: str,
                 prior_carryforward_as_of: date, rebuy: str = "none", notes: str | None = None,
                 today: date | None = None) -> dict:
    from services import tax_profiles as TP

    today = today or date.today()
    if (target_amount is None) == (not lots):
        raise C.Rep10Error("give a target amount OR the lots — one of the two")
    if itemizes not in ("yes", "no"):
        raise C.Rep10Error("itemizes is yes or no (entered, with its source)")
    if rebuy not in REBUY_LABELS:
        raise C.Rep10Error(f"rebuy is one of {list(REBUY_LABELS)}")
    if agi_estimate is not None and (agi_estimate <= 0 or not (agi_source or "").strip() or agi_as_of is None):
        raise C.Rep10Error("an AGI estimate is above zero and carries its source and as-of")
    if prior_carryforward is None or prior_carryforward < 0:
        raise C.Rep10Error("the prior carryforward is zero or more")
    _req_text(itemizes_source, "itemizes")
    _req_text(prior_carryforward_source, "the prior carryforward")
    async with pool.acquire() as conn:
        actor = await C.human(conn, actor_id, org_id)
        subj = await TP.check_subject(conn, org_id, subject_type, subject_id)
        await _member_in_subject(conn, org_id, subj, member_entity_id)
        donee = await _donee(conn, org_id, donee_id)
        if donee["status"] != "active":
            raise C.Rep10Conflict("the donee is retired")
        cand = await candidates(conn, org_id, subj["subject_type"], subj["subject_id"], today=today)
        by_id = {c["tax_lot_id"]: c for c in cand["candidates"]}
        if lots:
            chosen = []
            for x in lots:
                lid = C.uid(x.get("tax_lot_id"), "lot")
                if lid not in by_id:
                    why = next((e["reason"] for e in cand["excluded"] if e["tax_lot_id"] == lid), "not a lot of this "
                                                                                                   "subject")
                    raise C.Rep10Error(f"lot {lid} cannot be given in kind: {why}")
                q = x.get("quantity")
                q = Decimal(by_id[lid]["quantity"]) if q is None else q
                if not isinstance(q, Decimal) or q <= 0 or q > Decimal(by_id[lid]["quantity"]):
                    raise C.Rep10Error(f"lot {lid}: a quantity above zero and at most the lot's "
                                       f"{by_id[lid]['quantity']}")
                chosen.append({**by_id[lid], "quantity": str(q)})
        else:
            if not isinstance(target_amount, Decimal) or target_amount <= 0:
                raise C.Rep10Error("the target amount is a decimal above zero")
            chosen = pick_for_amount(cand["candidates"], target_amount)
            if not chosen:
                raise C.Rep10Conflict("no long-term lot at a gain is available to give")
        prof = await TP.approved_profile(conn, org_id, subj["subject_type"], subj["subject_id"], int(tax_year))
        rates = TP.combined_rates(prof) if prof else None
        comp = compare(chosen, rates=rates, donee_kind=donee["kind"], itemizes=itemizes, agi=agi_estimate,
                       prior_carryforward=prior_carryforward)
        hh = subj["subject_id"] if subj["subject_type"] == "household" else None
        async with conn.transaction():
            row = await conn.fetchrow(
                f"""INSERT INTO {T_GIFT} (org_id, household_id, subject_type, subject_id, member_entity_id, donee_id,
                        donee_kind, tax_year, target_amount, agi_estimate, agi_source, agi_as_of, itemizes,
                        itemizes_source, itemizes_as_of, prior_carryforward, prior_carryforward_source,
                        prior_carryforward_as_of, rebuy, comparison, rates, maker_id, notes)
                    VALUES ($1::uuid, $2::uuid, $3, $4::uuid, $5::uuid, $6::uuid, $7, $8, $9, $10, $11, $12, $13, $14,
                            $15, $16, $17, $18, $19, $20::jsonb, $21::jsonb, $22::uuid, $23) RETURNING *""",
                str(org_id), hh, subj["subject_type"], subj["subject_id"], C.uid(member_entity_id, "member"),
                str(donee["id"]), donee["kind"], int(tax_year), target_amount, agi_estimate,
                (agi_source or "").strip() or None, agi_as_of, itemizes, itemizes_source.strip(), itemizes_as_of,
                prior_carryforward, prior_carryforward_source.strip(), prior_carryforward_as_of, rebuy,
                C.dumps(comp), C.dumps(TP.rates_out(rates)), str(actor["id"]), (notes or "").strip() or None)
            for c in chosen:
                q, px, uc = Decimal(c["quantity"]), Decimal(c["price"]), Decimal(c["unit_cost"])
                await conn.execute(
                    f"""INSERT INTO {T_LOT} (org_id, gift_proposal_id, account_id, tax_lot_id, global_security_id,
                            quantity, unit_cost, acquired_date, holding_period, price_at_proposal, price_as_of,
                            price_source, fmv_at_proposal, gain_at_proposal)
                        VALUES ($1::uuid, $2::uuid, $3::uuid, $4::uuid, $5::uuid, $6, $7, $8, 'long', $9, $10, $11, $12,
                                $13)""",
                    str(org_id), str(row["id"]), c["account_id"], c["tax_lot_id"], c["global_security_id"], q, uc,
                    date.fromisoformat(c["acquired_date"]), px,
                    date.fromisoformat(c["price_as_of"]) if c["price_as_of"] else today, c["price_source"],
                    C.money(q * px), C.money((px - uc) * q))
            await C.audit(conn, org_id, actor["id"], "gift_created", SUBJECT, row["id"],
                          {"lots": len(chosen), "fmv": comp["fmv"], "donee_id": str(donee["id"])})
        return await detail(conn, org_id, row["id"], today=today)


def actions_for(g: dict, *, can_write: bool, can_approve: bool, user_id) -> list[str]:
    a, s = [], g["status"]
    me = str(user_id)
    if can_write:
        if s == "draft":
            a += ["submit", "cancel"]
        if s in ("pending_approval", "approved", "sent"):
            a.append("cancel")
        if s in ("approved", "sent", "received", "acknowledged"):
            a.append("letter")
        if s == "approved":
            a.append("mark_sent")
        if s == "sent":
            a.append("mark_received")
        if s == "received":
            a.append("acknowledge")
        if s in ("received", "acknowledged") and g["rebuy"] != "none" and not g["rebuy_proposal_ids"]:
            a.append("rebuy")
    if s == "pending_approval" and can_approve and me not in (g["maker_id"], g.get("submitted_by")):
        a += ["approve", "reject"]
    return a


async def submit(pool, *, org_id, actor_id, gift_id) -> dict:
    async with pool.acquire() as conn:
        actor = await C.human(conn, actor_id, org_id)
        g = await load(conn, org_id, gift_id)
        if g["status"] != "draft":
            raise C.Rep10Conflict("only a draft gift proposal is submitted")
        fmv = (C.loads(g["comparison"]) or {}).get("fmv")
    task = await HT.open_task(pool, org_id=org_id, kind=TASK_KIND, subject_type=SUBJECT, subject_id=g["id"],
                              maker_id=actor["id"], eligible_permission=APPROVE,
                              title=f"Approve charitable gift ({fmv} in kind, {g['tax_year']})",
                              payload={"fmv": fmv, "tax_year": g["tax_year"], "donee_kind": g["donee_kind"]},
                              action_path=f"/portfolio/tax?view=gifts&gift={g['id']}")
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                row = await C.update(conn, T_GIFT, g["id"], {"status": "pending_approval", "submitted_by": actor["id"],
                                                             "submitted_at": datetime.now(timezone.utc),
                                                             "approval_task_id": uuid.UUID(task["id"])})
                await C.audit(conn, org_id, actor["id"], "gift_submitted", SUBJECT, g["id"], {"task_id": task["id"]})
    except Exception:
        await C.withdraw_task(pool, task["id"], "the gift proposal could not be submitted")
        raise
    return {**gift_out(row), "task": task}


async def decide(pool, *, org_id, actor_id, gift_id, outcome: str, note: str | None = None) -> dict:
    if outcome not in ("approve", "reject"):
        raise C.Rep10Error("outcome is approve or reject")
    if outcome == "reject" and not (note or "").strip():
        raise C.Rep10Error("a rejection needs a note saying why")
    async with pool.acquire() as conn:
        actor = await C.human(conn, actor_id, org_id)
        g = await load(conn, org_id, gift_id)
    if g["status"] != "pending_approval":
        raise C.Rep10Conflict(f"the gift proposal is {STATUS_LABELS[g['status']].lower()}, not awaiting approval")
    if str(actor["id"]) in (str(g["maker_id"]), str(g["submitted_by"])):
        raise C.MakerCheckerViolation(f"maker-checker: you made this gift proposal, so a different holder of {APPROVE} "
                                      f"must {outcome} it")
    await C.complete(pool, org_id=org_id, task_id=g["approval_task_id"], actor_id=actor["id"], outcome=outcome,
                     note=note)
    async with pool.acquire() as conn:
        return await detail(conn, org_id, g["id"])


async def _apply(pool, task: dict, outcome: str, actor_id, note) -> dict | None:
    org_id = task["org_id"]
    async with pool.acquire() as conn:
        async with conn.transaction():
            g = await conn.fetchrow(f"SELECT * FROM {T_GIFT} WHERE id = $1::uuid AND org_id = $2::uuid AND "
                                    "system_to IS NULL FOR UPDATE", task["subject_id"], str(org_id))
            if g is None or g["status"] != "pending_approval":
                return None
            checker = uuid.UUID(str(actor_id))
            if checker in (g["maker_id"], g["submitted_by"]):
                raise C.MakerCheckerViolation("maker-checker: the maker of a gift proposal cannot approve it")
            now = datetime.now(timezone.utc)
            if outcome == "approve":
                fields = {"status": "approved", "approved_by": checker, "approved_at": now, "decision_note": note}
            else:
                fields = {"status": "cancelled", "cancelled_by": checker, "cancelled_at": now,
                          "cancel_reason": f"rejected: {note}", "decision_note": note}
            row = await C.update(conn, T_GIFT, g["id"], fields)
            await C.audit(conn, org_id, checker, f"gift_{'approved' if outcome == 'approve' else 'rejected'}", SUBJECT,
                          g["id"], {"task_id": task["id"], "note": note})
    return gift_out(row)


async def _on_complete(pool, task, outcome, actor_id, note):
    return await _apply(pool, task, outcome, actor_id, note)


HT.register_kind(TASK_KIND, label="Charitable gift approval", outcomes=("approve", "reject"), maker_checker=True,
                 on_complete=_on_complete)


async def _move(pool, org_id, actor_id, gift_id, allowed: tuple, fields_fn, action: str, payload: dict) -> dict:
    async with pool.acquire() as conn:
        actor = await C.human(conn, actor_id, org_id)
        g = await load(conn, org_id, gift_id)
        if g["status"] not in allowed:
            raise C.Rep10Conflict(f"cannot {action.replace('_', ' ')} a gift that is "
                                  f"{STATUS_LABELS[g['status']].lower()}")
        async with conn.transaction():
            await C.update(conn, T_GIFT, g["id"], fields_fn(actor, g))
            await C.audit(conn, org_id, actor["id"], f"gift_{action}", SUBJECT, g["id"], payload)
        return await detail(conn, org_id, g["id"])


async def mark_sent(pool, *, org_id, actor_id, gift_id, channel: str, note: str | None = None) -> dict:
    if not (channel or "").strip():
        raise C.Rep10Error("say how the letter was sent (e.g. email to the custodian, the member's portal)")
    return await _move(pool, org_id, actor_id, gift_id, ("approved",),
                       lambda a, g: {"status": "sent", "sent_by": a["id"], "sent_at": datetime.now(timezone.utc),
                                     "sent_channel": channel.strip()}, "sent", {"channel": channel, "note": note})


async def mark_received(pool, *, org_id, actor_id, gift_id, received_on: date, note: str | None = None,
                        prices: dict | None = None, today: date | None = None) -> dict:
    """The date the donee's account RECEIVED the shares — the gift date. The FMV at receipt per lot: the price a
    person entered (with its source), else the layer's eod_price ON that date, else "not estimated — needs prices"."""
    from services import security_data as SD

    today = today or date.today()
    if not isinstance(received_on, date) or received_on > today:
        raise C.Rep10Error("the received date is a date, not in the future")
    async with pool.acquire() as conn:
        actor = await C.human(conn, actor_id, org_id)
        g = await load(conn, org_id, gift_id)
        if g["status"] != "sent":
            raise C.Rep10Conflict(f"cannot mark received a gift that is {STATUS_LABELS[g['status']].lower()}")
        if g["sent_at"] and received_on < g["sent_at"].date():
            raise C.Rep10Error("the shares cannot be received before the letter was sent")
        lots = await lots_of(conn, g["id"])
        entered = {str(k): v for k, v in (prices or {}).items()}
        async with conn.transaction():
            for x in lots:
                sec = str(x["global_security_id"])
                px, src = None, None
                if sec in entered:
                    px, src = entered[sec]["price"], f"entered: {entered[sec]['source']}"
                else:
                    r = await SD.get(conn, org_id, "eod_price", sec, as_of=received_on, purpose="internal",
                                     max_age_days=0)
                    if r.found and r.value is not None and Decimal(str(r.value)) > 0:
                        px, src = Decimal(str(r.value)), f"security data: {r.provider_key} as of {C.iso(r.as_of)}"
                if px is None:
                    continue
                await conn.execute(f"UPDATE {T_LOT} SET valid_to = now() WHERE id = $1", x["id"])
                await conn.execute(
                    f"""INSERT INTO {T_LOT} (org_id, gift_proposal_id, account_id, tax_lot_id, global_security_id,
                            quantity, unit_cost, acquired_date, holding_period, price_at_proposal, price_as_of,
                            price_source, fmv_at_proposal, gain_at_proposal, fmv_at_receipt, receipt_price,
                            receipt_price_source)
                        SELECT org_id, gift_proposal_id, account_id, tax_lot_id, global_security_id, quantity, unit_cost,
                               acquired_date, holding_period, price_at_proposal, price_as_of, price_source,
                               fmv_at_proposal, gain_at_proposal, $2, $3, $4
                        FROM {T_LOT} WHERE id = $1""",
                    x["id"], C.money(Decimal(x["quantity"]) * px), px, src)
            await C.update(conn, T_GIFT, g["id"], {"status": "received", "received_on": received_on,
                                                   "received_by": actor["id"], "received_note": note})
            await C.audit(conn, org_id, actor["id"], "gift_received", SUBJECT, g["id"],
                          {"received_on": received_on.isoformat(), "note": note})
        return await detail(conn, org_id, g["id"])


async def acknowledge(pool, *, org_id, actor_id, gift_id, acknowledged_on: date, reference: str | None = None) -> dict:
    if not isinstance(acknowledged_on, date):
        raise C.Rep10Error("the acknowledgement date is a date")
    return await _move(pool, org_id, actor_id, gift_id, ("received",),
                       lambda a, g: {"status": "acknowledged", "acknowledged_on": acknowledged_on,
                                     "acknowledged_by": a["id"], "acknowledgement_ref": (reference or "").strip() or None},
                       "acknowledged", {"acknowledged_on": acknowledged_on.isoformat(), "reference": reference})


async def cancel(pool, *, org_id, actor_id, gift_id, reason: str) -> dict:
    if not (reason or "").strip():
        raise C.Rep10Error("say why the gift is cancelled")
    async with pool.acquire() as conn:
        g = await load(conn, org_id, gift_id)
    out = await _move(pool, org_id, actor_id, gift_id, ("draft", "pending_approval", "approved", "sent"),
                      lambda a, g_: {"status": "cancelled", "cancelled_by": a["id"],
                                     "cancelled_at": datetime.now(timezone.utc), "cancel_reason": reason.strip()},
                      "cancelled", {"reason": reason})
    if g["status"] == "pending_approval" and g["approval_task_id"]:
        await C.withdraw_task(pool, g["approval_task_id"], "the gift proposal was cancelled")
    return out


async def rebuy(pool, *, org_id, actor_id, gift_id, today: date | None = None) -> dict:
    """The rebuy as rep5 proposals (one per account that gave): same security → the account's target with the gifted
    quantity bought back from cash (a one-off buy for an advise account); replica-consistent → the account's normal
    proposal. Each is a proposal a person approves; nothing is bought here."""
    from services import account_models as AM
    from services import rebalance_proposals as RP

    today = today or date.today()
    async with pool.acquire() as conn:
        actor = await C.human(conn, actor_id, org_id)
        g = await load(conn, org_id, gift_id)
        if g["status"] not in ("received", "acknowledged"):
            raise C.Rep10Conflict("the rebuy follows the donee's receipt")
        if g["rebuy"] == "none":
            raise C.Rep10Conflict("this gift was proposed with no rebuy")
        if g["rebuy_proposal_ids"]:
            raise C.Rep10Conflict("the rebuy proposals were already made")
        lots = await lots_of(conn, g["id"])
        by_acct: dict[str, dict[str, Decimal]] = {}
        for x in lots:
            a = by_acct.setdefault(str(x["account_id"]), {})
            a[str(x["global_security_id"])] = a.get(str(x["global_security_id"]), C.ZERO) + Decimal(x["quantity"])
        accounts = {aid: await AM.load_account(conn, org_id, aid) for aid in by_acct}
    made = []
    for aid, secs in by_acct.items():
        acct = accounts[aid]
        ref = {"gift_rebuy": {"gift_id": str(g["id"]), "rebuy": g["rebuy"]}}
        if acct.get("authority") == "advise":
            lines = [{"global_security_id": k, "side": "buy", "quantity": q} for k, q in secs.items()]
            p = await RP.generate_one_off(pool, org_id=org_id, account_id=aid, maker_id=actor["id"], lines=lines,
                                          executor_type="member", note=f"charitable gift rebuy ({g['rebuy']})",
                                          today=today)
        elif g["rebuy"] == "replica_consistent":
            p = await RP.generate(pool, org_id=org_id, account_id=aid, trigger="manual", maker_kind="user",
                                  maker_id=actor["id"], trigger_ref=ref, today=today)
        else:
            async with pool.acquire() as conn:
                hold = await AM.load_holdings(conn, org_id, aid)
                tgt, labels = await RP._one_off_target(
                    conn, hold, [{"global_security_id": k, "side": "buy", "quantity": q} for k, q in secs.items()],
                    today, org_id=org_id)
                link = await AM.active_link(conn, aid)
                if link is None:
                    raise C.Rep10Conflict(f"{acct['account_number_masked']} follows no model — rebuy needs a managed "
                                          "account on a model (or an advise one-off)")
                base = await RP.target_for(conn, org_id, link, (await AM.model_brief(conn, org_id, link["model_id"]))[
                    "live_version_id"], adjust=False)
            override = {**base, "target": tgt, "labels": {**base["labels"], **labels}}
            p = await RP.generate(pool, org_id=org_id, account_id=aid, trigger="manual", maker_kind="user",
                                  maker_id=actor["id"], trigger_ref=ref, today=today, target_override=override)
        made.append(uuid.UUID(str(p["id"])))
    async with pool.acquire() as conn:
        async with conn.transaction():
            await C.update(conn, T_GIFT, g["id"], {"rebuy_proposal_ids": made})
            await C.audit(conn, org_id, actor["id"], "gift_rebuy", SUBJECT, g["id"],
                          {"proposal_ids": [str(x) for x in made], "rebuy": g["rebuy"]})
        out = await detail(conn, org_id, g["id"])
    out["rebuy_note"] = REBUY_NOTE
    return out


async def letter(pool, *, org_id, actor_id, gift_id) -> tuple[bytes, str]:
    """The transfer-of-securities letter, on demand, never stored; the request is audited."""
    from services import gift_letter as GL

    async with pool.acquire() as conn:
        actor = await C.human(conn, actor_id, org_id)
        g = await load(conn, org_id, gift_id)
        if g["status"] not in ("approved", "sent", "received", "acknowledged"):
            raise C.Rep10Conflict("the transfer letter is printed once the gift is approved")
        donee = donee_out(await _donee(conn, org_id, g["donee_id"]))
        lots = await lots_of(conn, g["id"])
        accts = {}
        for x in lots:
            aid = str(x["account_id"])
            if aid not in accts:
                a = await conn.fetchrow("SELECT account_number_masked, custodian_code FROM public.accounts WHERE id = "
                                        "$1::uuid AND org_id = $2::uuid AND system_to IS NULL ORDER BY valid_to NULLS "
                                        "FIRST LIMIT 1", aid, str(org_id))
                accts[aid] = {"account_number_masked": a["account_number_masked"],
                              "custodian_code": a["custodian_code"], "lots": []}
            name = await conn.fetchval("SELECT name FROM portfolio.securities_global WHERE id = $1",
                                       x["global_security_id"])
            accts[aid]["lots"].append({"security": name, "quantity": str(x["quantity"]),
                                       "acquired_date": C.iso(x["acquired_date"])})
        org_name = await conn.fetchval("SELECT name FROM organizations WHERE id = $1::uuid", str(org_id))
        prepared = actor.get("full_name") or actor.get("email") or ""
        await C.audit(conn, org_id, actor["id"], "gift_letter", SUBJECT, g["id"], {"accounts": len(accts)})
    pdf = GL.build_letter({"id": str(g["id"]), "tax_year": g["tax_year"], "donee": donee,
                           "accounts": list(accts.values())}, org_name=org_name or "", prepared_by=prepared)
    return pdf, f"gift-transfer-{str(g['id'])[:8]}.pdf"


# ═══ Year-end reminder sweep (services.rep10_sweeps) ═══════════════════════
async def year_end_sweep(pool, *, org_id, today: date) -> list[str]:
    """A to-do for every current-year gift not received by the firm's date (approved or sent)."""
    out = []
    async with pool.acquire() as conn:
        by = await receive_by(conn, org_id, today.year)
        if today < by:
            return out
        rows = await conn.fetch(f"SELECT id, tax_year, status FROM {T_GIFT} WHERE org_id = $1::uuid AND tax_year = $2 "
                                "AND status IN ('approved', 'sent') AND system_to IS NULL", str(org_id), today.year)
        recipients = await C.org_holders(conn, org_id, MANAGE)
        for r in rows:
            got = await C.todo(conn, org_id, recipients, source=TODO_YEAR_END, related_type=SUBJECT,
                               related_id=r["id"], title=f"Charitable gift not yet received ({r['tax_year']})",
                               detail=f"Not received by {by.isoformat()} — the shares must reach the donee's account "
                                      f"by 31 December for a {r['tax_year']} deduction. Follow up with the custodian.",
                               action_key=f"/portfolio/tax?view=gifts&gift={r['id']}", category="tax")
            if got:
                out.append(str(r["id"]))
    return out


def vocabularies() -> dict:
    return {"donee_kinds": [{"key": k, "label": v} for k, v in DONEE_KIND_LABELS.items()],
            "gift_statuses": [{"key": k, "label": v} for k, v in STATUS_LABELS.items()],
            "rebuy_choices": [{"key": k, "label": v} for k, v in REBUY_LABELS.items()],
            "exclusion_reasons": [{"key": k, "label": v} for k, v in EXCLUSION_LABELS.items()],
            "agi_limits": {k: {"in_kind": str(a), "cash": str(b)} for k, (a, b) in AGI_LIMITS.items()},
            "open_items": list(OPEN_ITEMS), "rebuy_note": REBUY_NOTE, "estimate_label": C.ESTIMATE,
            "delivery": "manual — a transfer letter the member signs; nothing is delivered electronically"}
