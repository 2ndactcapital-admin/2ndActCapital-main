"""CASH01 — the obligations ledger: every known future outflow per entity (the payer), stored + derived, ONE read.

STORED vs DERIVED (docs/cash/CASH_OBLIGATIONS.md). Only what a person records is a row in ``public.cash_obligations``:
manual items (taxes, property carrying, tuition, lifestyle, one-offs), a capital-call NOTICE, a vendor invoice, and a
known debt-service SCHEDULE for a liability. Everything else is DERIVED here at read time from its source and never
copied into the table:

* ``capital_call_projected`` — the TA model on portfolio.commitments (services.ta_state + ta_model.project_cash_flows,
  the same state GET /modeling/ta/projection uses). A noticed call for the same commitment SUPERSEDES the projected
  period nearest its date: the projection stays in the list (``counted = False``, ``superseded_by``) and is never
  counted twice. A commitment with no TA parameters reads "not estimated — needs a TA strategy".
* ``debt_service`` — portfolio.liabilities: a stored schedule for the liability (source_ref = the liability's
  LINEAGE id, stable across its versions) wins; otherwise an interest-only estimate from a fixed rate in its terms
  (balance × rate / 12, monthly, to maturity); a floating rate on an index the app does not carry, or terms with no
  rate, read "not estimated — needs payment terms".
* ``trust_payment`` / ``insurance_premium`` / ``charitable_payout`` — CRM detail fields (services.crm_vocab
  ``detail.*``): a GRAT's annuity (amount + next payment, yearly through Annuity end), a CRUT's unitrust (rate × the
  trust's value from the net-worth core, yearly), an ILIT's premium (amount + next due, yearly), a foundation's minimum
  distribution (rate × value, yearly).

RECURRENCES are an RRULE subset expanded at read time over the horizon (default 24 months), never stored as rows:
FREQ=MONTHLY|YEARLY|WEEKLY with INTERVAL, COUNT, UNTIL and (YEARLY) BYMONTH. Month-end and leap day are CLAMPED from
the ANCHOR (``due_date``): Jan 31 monthly → Feb 28/29, Mar 31, Apr 30, …; Feb 29 yearly → Feb 28 in a common year,
Feb 29 in a leap year. ``end_date`` (or ending the obligation) stops it.

Bi-temporal (Rule 3), valid axis: a revision closes the live row and inserts a new one with the same ``lineage_id``;
ending closes it with an ``end_reason``. Money is Decimal end to end; strings only at the JSON boundary. Runs on the
caller's connection under its org RLS context. No model call, no network.
"""

from __future__ import annotations

import calendar
import re
import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from uuid import UUID

TABLE = "public.cash_obligations"
CENTS = Decimal("0.01")
ZERO = Decimal(0)
MAX_AMOUNT = Decimal("1000000000000")
DEFAULT_HORIZON_MONTHS = 24
MAX_HORIZON_MONTHS = 120

#: every source type, stored or derived, with its label (published in the envelope — the screen never hardcodes it)
SOURCE_TYPES = (
    ("capital_call_projected", "Capital call — projected (TA model)"),
    ("capital_call_noticed", "Capital call — noticed"),
    ("debt_service", "Debt service"),
    ("tax_estimate", "Estimated tax"),
    ("insurance_premium", "Insurance premium"),
    ("trust_payment", "Trust payment"),
    ("charitable_payout", "Charitable payout"),
    ("property_carrying", "Property carrying cost"),
    ("tuition", "Tuition"),
    ("lifestyle_recurring", "Lifestyle (recurring)"),
    ("one_off", "One-off"),
    ("vendor_invoice", "Vendor invoice"),
)
SOURCE_LABELS = dict(SOURCE_TYPES)
#: what a person may record as a row (the table's CHECK carries the same list; a projection is never stored)
STORED_SOURCE_TYPES = frozenset(k for k, _ in SOURCE_TYPES if k != "capital_call_projected")
#: what the manual add / edit form offers (a notice and an invoice have their own paths)
MANUAL_SOURCE_TYPES = ("tax_estimate", "property_carrying", "tuition", "lifestyle_recurring", "one_off",
                       "insurance_premium", "trust_payment", "charitable_payout", "debt_service")
CERTAINTIES = (("firm", "Firm"), ("estimated", "Estimated"))
CERTAINTY_KEYS = frozenset(k for k, _ in CERTAINTIES)
REF_TYPES = ("commitment", "liability", "document", "entity", "spv_subscription")
FIELDS = ("entity_id", "source_type", "name", "amount", "currency_code", "due_date", "rrule", "end_date", "certainty",
          "source_ref_type", "source_ref_id", "owner_user_id", "notes")
REVISABLE = tuple(f for f in FIELDS if f != "entity_id")

COLS = ("id, lineage_id, org_id, entity_id, source_type, name, amount, currency_code, due_date, rrule, end_date, "
        "certainty, source_ref_type, source_ref_id, owner_user_id, notes, external_key, as_of, change_reason, "
        "end_reason, created_by, created_at, valid_from, valid_to")

#: a rate written in a liability's terms: "5.35% fixed, 2049" → 5.35. An INDEX word (SOFR + 1.10%) means floating on
#: a reference rate the app does not carry → needs payment terms (never a guessed rate).
RATE_RX = re.compile(r"(\d{1,2}(?:\.\d{1,4})?)\s*%")
INDEX_RX = re.compile(r"\b(SOFR|LIBOR|PRIME|SONIA|EURIBOR|TREASURY|T-BILL|FED\s*FUNDS|BASE\s*RATE|INDEX|CMT)\b|\+",
                      re.I)


class ObligationError(ValueError):
    """Refused for a reason the caller can fix (422)."""


class ObligationNotFound(LookupError):
    """No live obligation with that id in the caller's org (404)."""


def _u(v) -> UUID:
    return v if isinstance(v, UUID) else UUID(str(v))


def q(v: Decimal) -> Decimal:
    return v.quantize(CENTS, rounding=ROUND_HALF_UP)


def money_text(v: Decimal | None) -> str | None:
    return None if v is None else format(q(v), "f")


# ═══ Dates and recurrences (pure) ═══════════════════════════════════════════════════════════════════════════════
def add_months(d: date, n: int, day: int | None = None) -> date:
    """``d`` moved ``n`` months, on ``day`` (default d.day) CLAMPED to the month's last day."""
    m = d.month - 1 + n
    y, m = d.year + m // 12, m % 12 + 1
    last = calendar.monthrange(y, m)[1]
    return date(y, m, min(day or d.day, last))


def parse_rrule(rrule: str | None) -> dict | None:
    if rrule is None or str(rrule).strip() == "":
        return None
    parts = {}
    for chunk in str(rrule).strip().upper().split(";"):
        if "=" not in chunk:
            raise ObligationError(f"recurrence: '{chunk}' is not KEY=VALUE")
        k, v = chunk.split("=", 1)
        parts[k] = v
    freq = parts.get("FREQ")
    if freq not in ("MONTHLY", "YEARLY", "WEEKLY"):
        raise ObligationError("recurrence: FREQ is MONTHLY, YEARLY or WEEKLY")
    unknown = set(parts) - {"FREQ", "INTERVAL", "COUNT", "UNTIL", "BYMONTH"}
    if unknown:
        raise ObligationError(f"recurrence: {', '.join(sorted(unknown))} is not supported (FREQ, INTERVAL, COUNT, "
                              "UNTIL, BYMONTH)")
    out = {"freq": freq, "interval": 1, "count": None, "until": None, "bymonth": None}
    try:
        if "INTERVAL" in parts:
            out["interval"] = int(parts["INTERVAL"])
        if "COUNT" in parts:
            out["count"] = int(parts["COUNT"])
        if "UNTIL" in parts:
            u = parts["UNTIL"][:8]
            out["until"] = date(int(u[:4]), int(u[4:6]), int(u[6:8]))
        if "BYMONTH" in parts:
            out["bymonth"] = sorted({int(x) for x in parts["BYMONTH"].split(",")})
    except (ValueError, IndexError):
        raise ObligationError("recurrence: INTERVAL / COUNT are whole numbers, UNTIL is YYYYMMDD, BYMONTH is 1–12")
    if out["interval"] < 1 or out["interval"] > 60 or (out["count"] is not None and out["count"] < 1):
        raise ObligationError("recurrence: INTERVAL is 1–60 and COUNT at least 1")
    if out["bymonth"] is not None and (freq != "YEARLY" or any(m < 1 or m > 12 for m in out["bymonth"])):
        raise ObligationError("recurrence: BYMONTH (1–12) goes with FREQ=YEARLY")
    return out


def occurrences(anchor: date, rrule: str | None, *, end_date: date | None, start: date, until: date) -> list[date]:
    """The obligation's dates in [start, until] (inclusive). One-off: the anchor. Pure, deterministic."""
    rule = parse_rrule(rrule)
    stop = until if end_date is None else min(until, end_date)
    if rule is None:
        return [anchor] if start <= anchor <= stop else []
    if rule["until"] is not None:
        stop = min(stop, rule["until"])
    out: list[date] = []
    n = 0
    i = 0
    while True:
        if rule["freq"] == "WEEKLY":
            dates = [anchor + timedelta(weeks=i * rule["interval"])]
        elif rule["freq"] == "MONTHLY":
            dates = [add_months(anchor, i * rule["interval"], anchor.day)]
        elif rule["bymonth"]:
            y = anchor.year + i * rule["interval"]
            dates = [date(y, m, min(anchor.day, calendar.monthrange(y, m)[1])) for m in rule["bymonth"]]
            dates = [d for d in dates if d >= anchor]
        else:
            dates = [add_months(anchor, 12 * i * rule["interval"], anchor.day)]
        if not dates and i > 0 and rule["freq"] != "YEARLY":
            break
        past_stop = False
        for d in dates:
            if d > stop:
                past_stop = True
                break
            n += 1
            if rule["count"] is not None and n > rule["count"]:
                past_stop = True
                break
            if d >= start:
                out.append(d)
        if past_stop:
            break
        i += 1
        if i > 5000:
            break
    return out


# ═══ Validation + writers (stored rows) ═════════════════════════════════════════════════════════════════════════
def parse_money(raw, label: str = "Amount") -> Decimal:
    if raw is None or (isinstance(raw, str) and raw.strip() == ""):
        raise ObligationError(f"{label} is required")
    if isinstance(raw, (bool, float)):
        raise ObligationError(f"{label}: send the amount as text (e.g. \"125000.00\") — a float is refused so no "
                              "rounding enters")
    try:
        v = Decimal(str(raw).strip().replace(",", ""))
    except InvalidOperation:
        raise ObligationError(f"{label}: an amount, not '{raw}'") from None
    if not v.is_finite() or v < 0:
        raise ObligationError(f"{label}: zero or more")
    if v > MAX_AMOUNT:
        raise ObligationError(f"{label}: larger than any plausible obligation")
    if v != v.quantize(CENTS):
        raise ObligationError(f"{label}: at most two decimal places")
    return v.quantize(CENTS)


def _date(raw, label: str, *, required: bool) -> date | None:
    if raw is None or raw == "":
        if required:
            raise ObligationError(f"{label} is required")
        return None
    if isinstance(raw, date):
        return raw
    try:
        return date.fromisoformat(str(raw))
    except ValueError:
        raise ObligationError(f"{label}: a date as YYYY-MM-DD, not '{raw}'") from None


def _text(raw, limit: int) -> str | None:
    if raw is None:
        return None
    s = str(raw).strip()
    if len(s) > limit:
        raise ObligationError(f"text longer than {limit} characters")
    return s or None


async def _validate(conn, org_id, data: dict, *, allowed_types=STORED_SOURCE_TYPES) -> dict:
    out: dict = {}
    st = data.get("source_type")
    if st not in allowed_types:
        raise ObligationError("source type is one of " + ", ".join(k for k, _ in SOURCE_TYPES if k in allowed_types))
    out["source_type"] = st
    out["name"] = _text(data.get("name"), 300)
    if not out["name"]:
        raise ObligationError("name the obligation (e.g. \"Q2 estimated federal tax\")")
    out["amount"] = parse_money(data.get("amount"))
    cur = (data.get("currency_code") or "USD").strip().upper()
    if len(cur) != 3 or not cur.isalpha():
        raise ObligationError("currency is a three-letter code, e.g. USD")
    out["currency_code"] = cur
    out["due_date"] = _date(data.get("due_date"), "Due date", required=True)
    out["end_date"] = _date(data.get("end_date"), "End date", required=False)
    if out["end_date"] is not None and out["end_date"] < out["due_date"]:
        raise ObligationError("the end date is on or after the (first) due date")
    rr = _text(data.get("rrule"), 200)
    rr = rr.upper() if rr else None
    parse_rrule(rr)
    out["rrule"] = rr
    c = data.get("certainty")
    if c not in CERTAINTY_KEYS:
        raise ObligationError("certainty is firm or estimated")
    out["certainty"] = c
    rt, rid = data.get("source_ref_type") or None, data.get("source_ref_id") or None
    if (rt is None) != (rid is None):
        raise ObligationError("a source reference names both its type and its record")
    if rt is not None and rt not in REF_TYPES:
        raise ObligationError("source reference type is one of " + ", ".join(REF_TYPES))
    if rid is not None:
        try:
            rid = _u(rid)
        except ValueError:
            raise ObligationError("source reference: choose a record") from None
    if st == "debt_service" and rt != "liability":
        raise ObligationError("a debt-service schedule names its liability")
    if st == "vendor_invoice" and rt != "document":
        raise ObligationError("a vendor invoice names its Chancery document")
    if st == "capital_call_noticed" and rt not in (None, "commitment", "spv_subscription"):
        raise ObligationError("a capital-call notice names its commitment (or none, when it matches no commitment)")
    if rt == "liability" and not await conn.fetchval(
            "SELECT 1 FROM portfolio.liabilities WHERE lineage_id = $1 AND org_id = $2::uuid AND valid_to IS NULL "
            "AND system_to IS NULL", rid, _u(org_id)):
        raise ObligationError("source reference: no live liability with that lineage in this organisation")
    if rt == "commitment" and not await conn.fetchval(
            "SELECT 1 FROM portfolio.commitments WHERE id = $1 AND org_id = $2::uuid AND valid_to IS NULL "
            "AND system_to IS NULL", rid, _u(org_id)):
        raise ObligationError("source reference: no live commitment with that id in this organisation")
    if rt == "document" and not await conn.fetchval(
            "SELECT 1 FROM documents WHERE id = $1 AND org_id = $2::uuid", rid, _u(org_id)):
        raise ObligationError("source reference: no document with that id in this organisation")
    if rt == "spv_subscription" and not await conn.fetchval(
            "SELECT 1 FROM spv_subscriptions WHERE id = $1 AND org_id = $2::uuid AND valid_to IS NULL", rid, _u(org_id)):
        raise ObligationError("source reference: no live SPV subscription with that id in this organisation")
    if rt == "entity" and not await _entity_ok(conn, org_id, rid):
        raise ObligationError("source reference: no CRM record with that id in this organisation")
    out["source_ref_type"], out["source_ref_id"] = rt, rid
    owner = data.get("owner_user_id") or None
    if owner is not None:
        try:
            owner = _u(owner)
        except ValueError:
            raise ObligationError("owner: choose a person") from None
        if not await conn.fetchval("SELECT 1 FROM users WHERE id = $1 AND org_id = $2::uuid", owner, _u(org_id)):
            raise ObligationError("owner: not a person of this organisation")
    out["owner_user_id"] = owner
    out["notes"] = _text(data.get("notes"), 2000)
    return out


async def _entity_ok(conn, org_id, entity_id) -> bool:
    return bool(await conn.fetchval("SELECT 1 FROM entities WHERE id = $1 AND org_id = $2::uuid AND valid_to IS NULL "
                                    "AND system_to IS NULL", _u(entity_id), _u(org_id)))


def row_json(r) -> dict:
    d = {}
    for k, v in dict(r).items():
        if isinstance(v, Decimal):
            d[k] = format(v, "f")
        elif isinstance(v, UUID):
            d[k] = str(v)
        elif hasattr(v, "isoformat"):
            d[k] = v.isoformat()
        else:
            d[k] = v
    d["source_label"] = SOURCE_LABELS.get(r["source_type"], r["source_type"])
    d["ended"] = r["valid_to"] is not None
    return d


INSERT_SQL = f"""
INSERT INTO {TABLE} (lineage_id, org_id, entity_id, source_type, name, amount, currency_code, due_date, rrule,
                     end_date, certainty, source_ref_type, source_ref_id, owner_user_id, notes, external_key, as_of,
                     change_reason, created_by)
VALUES ($1, $2::uuid, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17::date, $18, $19)
RETURNING {COLS}
"""


async def create(conn, *, org_id, actor_id, data: dict, today: date, external_key: str | None = None,
                 allowed_types=STORED_SOURCE_TYPES, reason: str = "recorded on Treasury") -> dict:
    unknown = sorted(set(data) - set(FIELDS))
    if unknown:
        raise ObligationError(f"not an obligation field: {', '.join(unknown)}")
    if not data.get("entity_id"):
        raise ObligationError("choose the entity that pays (the payer)")
    try:
        entity_id = _u(data["entity_id"])
    except ValueError:
        raise ObligationError("payer: choose a CRM record") from None
    if not await _entity_ok(conn, org_id, entity_id):
        raise ObligationNotFound("Entity not found")
    v = await _validate(conn, org_id, data, allowed_types=allowed_types)
    row = await conn.fetchrow(INSERT_SQL, uuid.uuid4(), _u(org_id), entity_id, v["source_type"], v["name"],
                              v["amount"], v["currency_code"], v["due_date"], v["rrule"], v["end_date"],
                              v["certainty"], v["source_ref_type"], v["source_ref_id"], v["owner_user_id"],
                              v["notes"], external_key, today, reason, _u(actor_id) if actor_id else None)
    return row_json(row)


async def live(conn, org_id, obligation_id):
    return await conn.fetchrow(f"SELECT {COLS} FROM {TABLE} WHERE id = $1 AND org_id = $2::uuid AND valid_to IS NULL "
                               "AND system_to IS NULL", _u(obligation_id), _u(org_id))


async def revise(conn, *, org_id, actor_id, obligation_id, changes: dict, today: date,
                 reason: str | None = None) -> dict:
    """A new version: close the live row, insert the merged row with the same lineage. Validates the MERGED row."""
    cur = await live(conn, org_id, obligation_id)
    if cur is None:
        raise ObligationNotFound("Obligation not found, or already ended")
    unknown = sorted(set(changes) - set(REVISABLE))
    if unknown:
        raise ObligationError(f"not a revisable field: {', '.join(unknown)}")
    if not changes:
        raise ObligationError("nothing to change")
    merged = {k: cur[k] for k in REVISABLE}
    merged["amount"] = format(merged["amount"], "f")
    merged.update(changes)
    allowed = STORED_SOURCE_TYPES if cur["source_type"] == merged["source_type"] else frozenset(MANUAL_SOURCE_TYPES)
    v = await _validate(conn, org_id, merged, allowed_types=allowed | {cur["source_type"]})
    async with conn.transaction():
        n = await conn.execute(f"UPDATE {TABLE} SET valid_to = now() WHERE id = $1 AND org_id = $2::uuid AND "
                               "valid_to IS NULL AND system_to IS NULL", cur["id"], _u(org_id))
        if n != "UPDATE 1":
            raise ObligationNotFound("Obligation not found, or already ended (or the update was not permitted)")
        row = await conn.fetchrow(INSERT_SQL, cur["lineage_id"], _u(org_id), cur["entity_id"], v["source_type"],
                                  v["name"], v["amount"], v["currency_code"], v["due_date"], v["rrule"],
                                  v["end_date"], v["certainty"], v["source_ref_type"], v["source_ref_id"],
                                  v["owner_user_id"], v["notes"], cur["external_key"], today,
                                  _text(reason, 500) or "edited on Treasury", _u(actor_id) if actor_id else None)
    return row_json(row)


async def end(conn, *, org_id, obligation_id, reason: str | None = None) -> dict:
    row = await conn.fetchrow(
        f"UPDATE {TABLE} SET valid_to = now(), end_reason = $3 WHERE id = $1 AND org_id = $2::uuid "
        f"AND valid_to IS NULL AND system_to IS NULL RETURNING {COLS}",
        _u(obligation_id), _u(org_id), _text(reason, 500) or "ended (paid, cancelled or no longer expected)")
    if row is None:
        raise ObligationNotFound("Obligation not found, or already ended (or the update was not permitted)")
    return row_json(row)


async def stored_rows(conn, org_id, entity_ids) -> list[dict]:
    if not entity_ids:
        return []
    return [dict(r) for r in await conn.fetch(
        f"SELECT {COLS} FROM {TABLE} WHERE org_id = $1::uuid AND entity_id = ANY($2::uuid[]) AND valid_to IS NULL "
        "AND system_to IS NULL ORDER BY due_date, name, id", _u(org_id), [_u(e) for e in entity_ids])]


async def history(conn, org_id, lineage_id) -> list[dict]:
    return [row_json(r) for r in await conn.fetch(
        f"SELECT {COLS} FROM {TABLE} WHERE org_id = $1::uuid AND lineage_id = $2 AND system_to IS NULL "
        "ORDER BY valid_from, id", _u(org_id), _u(lineage_id))]


# ═══ The merged schedule ═══════════════════════════════════════════════════════════════════════════════════════
@dataclass
class Line:
    key: str
    date: date
    amount: Decimal
    currency: str
    certainty: str
    source_type: str
    payer_entity_id: str
    name: str
    as_of: date
    explanation: str
    derived: bool
    derived_from: str | None = None
    obligation_id: str | None = None
    lineage_id: str | None = None
    source_ref_type: str | None = None
    source_ref_id: str | None = None
    counted: bool = True
    superseded_by: str | None = None

    def to_json(self, names: dict | None = None) -> dict:
        names = names or {}
        return {"key": self.key, "date": self.date.isoformat(), "amount": money_text(self.amount),
                "currency": self.currency, "certainty": self.certainty, "source_type": self.source_type,
                "source_label": SOURCE_LABELS.get(self.source_type, self.source_type),
                "payer_entity_id": self.payer_entity_id, "payer_name": names.get(self.payer_entity_id),
                "name": self.name, "as_of": self.as_of.isoformat(), "explanation": self.explanation,
                "derived": self.derived, "derived_from": self.derived_from, "obligation_id": self.obligation_id,
                "lineage_id": self.lineage_id, "source_ref_type": self.source_ref_type,
                "source_ref_id": self.source_ref_id, "counted": self.counted, "superseded_by": self.superseded_by}


def interest_only_rate(rate_text: str | None) -> tuple[Decimal | None, str]:
    """(annual rate %, why) from a liability's terms. A rate on a reference INDEX is not estimated (no index data)."""
    t = (rate_text or "").strip()
    if not t:
        return None, "needs payment terms (no rate recorded)"
    if INDEX_RX.search(t):
        return None, f"needs payment terms (the rate '{t}' floats on an index the app does not carry)"
    m = RATE_RX.search(t)
    if m is None:
        return None, f"needs payment terms (no rate could be read from '{t}')"
    return Decimal(m.group(1)), f"estimated — interest only, rate {m.group(1)}% from terms '{t}'"


def _first_of_next_month(d: date) -> date:
    return add_months(date(d.year, d.month, 1), 1, 1)


async def _details(conn, org_id, entity_ids) -> dict[str, dict[str, str]]:
    rows = await conn.fetch(
        "SELECT entity_id::text AS e, attribute_key AS k, attribute_value AS v FROM entity_attributes "
        "WHERE org_id = $1::uuid AND entity_id = ANY($2::uuid[]) AND attribute_key LIKE 'detail.%' "
        "AND valid_to IS NULL AND system_to IS NULL", _u(org_id), [_u(e) for e in entity_ids])
    out: dict[str, dict[str, str]] = {}
    for r in rows:
        out.setdefault(r["e"], {})[r["k"][len("detail."):]] = r["v"]
    return out


def _dec(v) -> Decimal | None:
    if v is None:
        return None
    try:
        d = Decimal(str(v))
    except InvalidOperation:
        return None
    return d if d.is_finite() else None


def _iso(v) -> date | None:
    try:
        return date.fromisoformat(str(v)) if v else None
    except ValueError:
        return None


def _yearly(first: date, start: date, until: date, end: date | None = None) -> list[date]:
    return occurrences(first, "FREQ=YEARLY", end_date=end, start=start, until=until)


async def _projected_calls(conn, org_id, entity_ids, *, today: date, until: date, settings: dict) -> tuple[list, list]:
    """TA-projected calls for every live commitment owned by ``entity_ids`` (SPV-asset commitments excluded: the SPV
    ledger is their book, see CASH00 1a). Returns (lines, not_estimated)."""
    from services.ta_model import TAModelError, project_cash_flows
    from services.ta_state import TAStateError, resolve_commitment_state

    rows = await conn.fetch(
        """SELECT c.id::text AS id, p.owner_entity_id::text AS owner, c.unfunded, c.commitment_amount,
                  c.called_to_date, c.liquidity_terms->>'ta_strategy_key' AS strategy, COALESCE(g.name, a.name) AS name
             FROM portfolio.commitments c
             JOIN portfolio.positions p ON p.id = c.position_id AND p.org_id = c.org_id
             JOIN portfolio.assets a ON a.id = p.asset_id AND a.org_id = p.org_id
             LEFT JOIN portfolio.securities_global g ON g.id = a.global_security_id AND g.valid_to IS NULL
                   AND g.system_to IS NULL
            WHERE c.org_id = $1::uuid AND p.owner_entity_id = ANY($2::uuid[]) AND a.internal_spv_id IS NULL
              AND c.valid_to IS NULL AND c.system_to IS NULL AND p.valid_to IS NULL AND p.system_to IS NULL
            ORDER BY name, c.id""", _u(org_id), [_u(e) for e in entity_ids])
    lines, missing = [], []
    for r in rows:
        unfunded = r["unfunded"] if r["unfunded"] is not None else (
            (r["commitment_amount"] or ZERO) - (r["called_to_date"] or ZERO))
        if unfunded is None or unfunded <= 0:
            continue
        try:
            st = await resolve_commitment_state(conn, org_id=str(org_id), commitment_id=r["id"],
                                                strategy_key=r["strategy"], periods_per_year=None, settings=settings)
        except TAStateError as exc:
            missing.append({"source_type": "capital_call_projected", "payer_entity_id": r["owner"],
                            "name": f"{r['name']} — unfunded {money_text(unfunded)}", "amount": money_text(unfunded),
                            "source_ref_type": "commitment", "source_ref_id": r["id"],
                            "needs": "a TA strategy for the commitment (set its parameters on the projection screen)"
                            if exc.status == 422 else exc.detail})
            continue
        ppy = st["params"].periods_per_year
        step = max(1, 12 // ppy)
        horizon = 1
        while add_months(today, horizon * step) <= until and horizon < 400:
            horizon += 1
        try:
            periods = project_cash_flows(committed_capital=st["committed_capital"],
                                         called_to_date=st["called_to_date"],
                                         distributed_to_date=st["distributed_to_date"],
                                         current_nav=st["current_nav"], params=st["params"],
                                         horizon_periods=horizon)
        except TAModelError as exc:
            missing.append({"source_type": "capital_call_projected", "payer_entity_id": r["owner"],
                            "name": r["name"], "amount": money_text(unfunded), "source_ref_type": "commitment",
                            "source_ref_id": r["id"], "needs": f"valid TA parameters ({exc})"})
            continue
        for p in periods:
            d = add_months(today, p.period * step)
            if d > until or p.contribution <= 0:
                continue
            lines.append(Line(
                key=f"proj:{r['id']}:{p.period}", date=d, amount=p.contribution, currency="USD",
                certainty="estimated", source_type="capital_call_projected", payer_entity_id=r["owner"],
                name=f"{r['name']} — projected call", as_of=today, derived=True,
                derived_from=f"TA model ({st['ta_strategy_key']}) on the commitment",
                explanation=f"projected by the Takahashi-Alexander model: period {p.period} at {ppy} per year, "
                            f"from unfunded {money_text(st['committed_capital'] - st['called_to_date'])}",
                source_ref_type="commitment", source_ref_id=r["id"]))
    return lines, missing


async def _debt_service(conn, org_id, entity_ids, scheduled: set[str], *, today: date, until: date) -> tuple:
    rows = await conn.fetch(
        "SELECT id::text AS id, lineage_id::text AS lineage, owner_entity_id::text AS owner, name, kind, "
        "current_balance, original_amount, currency_code, as_of, rate_type, rate_text, maturity FROM "
        "portfolio.liabilities WHERE org_id = $1::uuid AND owner_entity_id = ANY($2::uuid[]) AND valid_to IS NULL "
        "AND system_to IS NULL ORDER BY name, id", _u(org_id), [_u(e) for e in entity_ids])
    lines, missing, lines_of_credit = [], [], []
    for r in rows:
        if r["kind"] in ("securities_backed_line", "asset_secured_line", "margin"):
            limit = r["original_amount"]
            lines_of_credit.append({
                "liability_id": r["id"], "lineage_id": r["lineage"], "payer_entity_id": r["owner"],
                "name": r["name"], "kind": r["kind"], "drawn": money_text(r["current_balance"]),
                "limit": money_text(limit),
                "undrawn": money_text(limit - r["current_balance"]) if limit is not None
                and limit >= r["current_balance"] else None,
                "securities_backed": r["kind"] in ("securities_backed_line", "margin"),
                "note": "undrawn = the approved limit (the line's Original amount) − the drawn balance; shown "
                        "separately, never counted as liquid" if limit is not None else
                        "not estimated — needs the line's approved limit (its Original amount)",
                "as_of": r["as_of"].isoformat()})
        if r["lineage"] in scheduled:
            continue        # a stored schedule for this liability wins (it is in the stored lines)
        rate, why = interest_only_rate(r["rate_text"])
        if rate is None or not r["current_balance"]:
            missing.append({"source_type": "debt_service", "payer_entity_id": r["owner"], "name": r["name"],
                            "amount": None, "source_ref_type": "liability", "source_ref_id": r["lineage"],
                            "needs": why.replace("needs ", "", 1) if rate is None else "a balance"})
            continue
        monthly = r["current_balance"] * rate / Decimal(100) / Decimal(12)
        end = min(until, r["maturity"]) if r["maturity"] else until
        for d in occurrences(_first_of_next_month(today), "FREQ=MONTHLY", end_date=end, start=today, until=until):
            lines.append(Line(
                key=f"debt:{r['lineage']}:{d.isoformat()}", date=d, amount=q(monthly), currency=r["currency_code"],
                certainty="estimated", source_type="debt_service", payer_entity_id=r["owner"],
                name=f"{r['name']} — interest", as_of=r["as_of"], derived=True,
                derived_from="the liability's balance and terms",
                explanation=f"{why}: {money_text(r['current_balance'])} × {rate}% ÷ 12",
                source_ref_type="liability", source_ref_id=r["lineage"]))
    return lines, missing, lines_of_credit


def _missing(*pairs) -> str:
    """'a and b' for the pairs whose value is None — a not-estimated line names exactly what is missing."""
    names = [n for n, v in pairs if v is None]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def _trust_kind(ent: dict, det: dict) -> str | None:
    tt = (det.get("trust_type") or "").lower()
    sub = (ent.get("sub_type") or "").strip().lower()
    for k in ("grat", "crut", "ilit"):
        if tt == k or sub == k:
            return k
    return None


async def _crm_payments(conn, org_id, entity_ids, names: dict, values: dict, *, today: date, until: date,
                        recorded: set | None = None) -> tuple:
    """``recorded`` = {(entity, source_type)} with a STORED row: the stored row wins and nothing is derived for that
    pair (the same rule as a debt-service schedule), so a manual stand-in is never counted twice."""
    det = await _details(conn, org_id, entity_ids)
    lines, missing = [], []
    recorded = recorded or set()
    for e in entity_ids:
        ent = names.get(e) or {}
        d = det.get(e, {})
        etype = ent.get("entity_type")
        kind = _trust_kind(ent, d) if etype == "trust" else None
        disp = ent.get("display_name") or e

        def need(st, what, amount=None):
            missing.append({"source_type": st, "payer_entity_id": e, "name": disp, "amount": amount,
                            "source_ref_type": "entity", "source_ref_id": e, "needs": what})

        if kind == "grat" and (e, "trust_payment") not in recorded:
            amt, first, end_ = _dec(d.get("annuity_amount")), _iso(d.get("annuity_payment_date")), _iso(
                d.get("annuity_end"))
            if amt is None or first is None:
                if end_ is None or end_ >= today:
                    need("trust_payment", "the GRAT's " + _missing(("annuity amount", amt), ("next annuity payment date",
                                                                             first)) + " (CRM detail fields)")
            else:
                for dt in _yearly(first, today, until, end_):
                    lines.append(Line(key=f"grat:{e}:{dt.isoformat()}", date=dt, amount=q(amt), currency="USD",
                                      certainty="firm", source_type="trust_payment", payer_entity_id=e,
                                      name=f"{disp} — annuity payment", as_of=today, derived=True,
                                      derived_from="the GRAT's CRM terms",
                                      explanation=f"annuity {money_text(amt)} a year from {first.isoformat()}"
                                                  + (f" through {end_.isoformat()}" if end_ else ""),
                                      source_ref_type="entity", source_ref_id=e))
        if kind == "crut" and (e, "trust_payment") not in recorded:
            rate, first = _dec(d.get("unitrust_rate_pct")), _iso(d.get("unitrust_payment_date"))
            val = values.get(e)
            if rate is None or first is None or val is None:
                need("trust_payment", "the CRUT's " + _missing(("unitrust rate", rate), ("next unitrust payment date",
                                                                                      first), ("value in net worth", val)))
            else:
                amt = q(val * rate / Decimal(100))
                for dt in _yearly(first, today, until):
                    lines.append(Line(key=f"crut:{e}:{dt.isoformat()}", date=dt, amount=amt, currency="USD",
                                      certainty="estimated", source_type="trust_payment", payer_entity_id=e,
                                      name=f"{disp} — unitrust payment", as_of=today, derived=True,
                                      derived_from="the CRUT's CRM terms × its value in net worth",
                                      explanation=f"estimated — {rate}% × {money_text(val)} (today's value)",
                                      source_ref_type="entity", source_ref_id=e))
        if etype == "trust" and (e, "insurance_premium") not in recorded and (
                kind == "ilit" or d.get("policy_face_amount") or d.get("policy_premium_amount")):
            amt, first = _dec(d.get("policy_premium_amount")), _iso(d.get("policy_premium_due"))
            if amt is None or first is None:
                need("insurance_premium", "the policy's " + _missing(("annual premium", amt), ("next premium due date", first))
                     + " (CRM insurance fields)")
            else:
                for dt in _yearly(first, today, until):
                    lines.append(Line(key=f"prem:{e}:{dt.isoformat()}", date=dt, amount=q(amt), currency="USD",
                                      certainty="firm", source_type="insurance_premium", payer_entity_id=e,
                                      name=f"{disp} — premium", as_of=today, derived=True,
                                      derived_from="the trust's insurance policy (CRM)",
                                      explanation=f"premium {money_text(amt)} a year from {first.isoformat()}",
                                      source_ref_type="entity", source_ref_id=e))
        if etype == "foundation" and (e, "charitable_payout") not in recorded:
            rate, first = _dec(d.get("min_distribution_pct")), _iso(d.get("distribution_due"))
            val = values.get(e)
            if rate is None or first is None or val is None:
                if rate is not None or first is not None:
                    need("charitable_payout", "the foundation's " + _missing(
                        ("minimum distribution %", rate), ("next minimum distribution due date", first),
                        ("value in net worth", val)))
            else:
                amt = q(val * rate / Decimal(100))
                for dt in _yearly(first, today, until):
                    lines.append(Line(key=f"found:{e}:{dt.isoformat()}", date=dt, amount=amt, currency="USD",
                                      certainty="estimated", source_type="charitable_payout", payer_entity_id=e,
                                      name=f"{disp} — minimum distribution", as_of=today, derived=True,
                                      derived_from="the foundation's CRM terms × its value in net worth",
                                      explanation=f"estimated — {rate}% × {money_text(val)} (today's value)",
                                      source_ref_type="entity", source_ref_id=e))
    return lines, missing


def supersede(projected: list[Line], notices: list[Line]) -> list[dict]:
    """A notice matched to a commitment supersedes that commitment's projected line NEAREST its date (the projected
    line stays, ``counted = False``). Returns the unmatched notices (no commitment, or none projected). Pure."""
    unmatched = []
    for n in sorted(notices, key=lambda x: (x.date, x.key)):
        if n.source_ref_type != "commitment" or not n.source_ref_id:
            unmatched.append({"lineage_id": n.lineage_id, "name": n.name, "payer_entity_id": n.payer_entity_id,
                              "date": n.date.isoformat(), "amount": money_text(n.amount),
                              "reason": "the notice names no commitment"})
            continue
        cands = [p for p in projected if p.source_ref_id == n.source_ref_id and p.counted]
        if not cands:
            continue    # matched to a commitment with nothing projected in the horizon: the notice alone counts
        best = min(cands, key=lambda p: (abs((p.date - n.date).days), p.date))
        best.counted = False
        best.superseded_by = n.lineage_id
        best.explanation += f" — superseded by the notice '{n.name}' ({n.date.isoformat()})"
    return unmatched


async def schedule(conn, org_id, entity_ids, *, today: date, horizon_months: int = DEFAULT_HORIZON_MONTHS,
                   names: dict | None = None, values: dict | None = None,
                   extra_stored: list[dict] | None = None) -> dict:
    """THE read: the merged schedule (stored + derived) for ``entity_ids`` over [today, today + horizon]. ``names``
    ({entity: {display_name, entity_type, sub_type}}) and ``values`` ({entity: Decimal gross}) come from the net-worth
    core when the caller already loaded it (services.cash_coverage does)."""
    from services.org_settings import get_all_settings

    if not 1 <= int(horizon_months) <= MAX_HORIZON_MONTHS:
        raise ObligationError(f"horizon is 1–{MAX_HORIZON_MONTHS} months")
    ents = sorted({str(e) for e in entity_ids})
    until = add_months(today, int(horizon_months))
    if names is None or values is None:
        from services import networth as NW

        ctx = await NW.load(conn, org_id, ents, as_of=today)
        names = names if names is not None else ctx.names
        values = values if values is not None else {e: NW.figures(ctx, weights={e: Decimal(1)})["gross"]
                                                    for e in ents}
    stored = await stored_rows(conn, org_id, ents)
    # an operator's --dry-run passes the rows it WOULD write (in memory, never inserted) to see their coverage
    stored += [r for r in (extra_stored or []) if str(r["entity_id"]) in set(ents)]
    lines: list[Line] = []
    notices: list[Line] = []
    scheduled_liabilities = set()
    for r in stored:
        if r["source_type"] == "debt_service" and r["source_ref_id"]:
            scheduled_liabilities.add(str(r["source_ref_id"]))
        for d in occurrences(r["due_date"], r["rrule"], end_date=r["end_date"], start=today, until=until):
            ln = Line(key=f"obl:{r['lineage_id']}:{d.isoformat()}", date=d, amount=r["amount"],
                      currency=r["currency_code"], certainty=r["certainty"], source_type=r["source_type"],
                      payer_entity_id=str(r["entity_id"]), name=r["name"], as_of=r["as_of"], derived=False,
                      explanation=("recurring: " + r["rrule"] if r["rrule"] else "recorded")
                      + (f" (ends {r['end_date'].isoformat()})" if r["end_date"] else ""),
                      obligation_id=str(r["id"]), lineage_id=str(r["lineage_id"]),
                      source_ref_type=r["source_ref_type"],
                      source_ref_id=str(r["source_ref_id"]) if r["source_ref_id"] else None)
            lines.append(ln)
            if r["source_type"] == "capital_call_noticed":
                notices.append(ln)
    unmatched_rows = [r for r in stored if r["source_type"] == "capital_call_noticed" and not r["source_ref_id"]]
    settings = await get_all_settings(conn, org_id)
    proj, miss_p = await _projected_calls(conn, org_id, ents, today=today, until=until, settings=settings)
    debt, miss_d, credit = await _debt_service(conn, org_id, ents, scheduled_liabilities, today=today, until=until)
    recorded = {(str(r["entity_id"]), r["source_type"]) for r in stored}
    crm, miss_c = await _crm_payments(conn, org_id, ents, names, values, today=today, until=until, recorded=recorded)
    supersede(proj, notices)
    lines += proj + debt + crm
    lines.sort(key=lambda x: (x.date, x.payer_entity_id, x.key))
    return {"today": today, "until": until, "horizon_months": int(horizon_months), "lines": lines,
            "not_estimated": miss_p + miss_d + miss_c, "lines_of_credit": credit,
            "unmatched_notices": [{"lineage_id": str(r["lineage_id"]), "obligation_id": str(r["id"]),
                                   "name": r["name"], "payer_entity_id": str(r["entity_id"]),
                                   "date": r["due_date"].isoformat(), "amount": money_text(r["amount"])}
                                  for r in unmatched_rows],
            "stored": stored}


def vocabularies(*, can_write: bool) -> dict:
    return {"source_types": [{"key": k, "label": v, "stored": k in STORED_SOURCE_TYPES} for k, v in SOURCE_TYPES],
            "manual_source_types": list(MANUAL_SOURCE_TYPES) if can_write else [],
            "certainties": [{"key": k, "label": v} for k, v in CERTAINTIES],
            "recurrence_examples": [{"rrule": "FREQ=MONTHLY", "label": "Monthly"},
                                    {"rrule": "FREQ=MONTHLY;INTERVAL=3", "label": "Quarterly"},
                                    {"rrule": "FREQ=YEARLY", "label": "Yearly"},
                                    {"rrule": "FREQ=YEARLY;BYMONTH=1,4,6,9", "label": "Estimated-tax dates"}],
            # CASH02: "minimum" is gone — the minimum lives in the cash policy (maker-checker), not a direct edit
            "editable": ["obligation", "notice"] if can_write else [],
            "inline_editable": []}
