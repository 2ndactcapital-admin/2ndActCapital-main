"""The loss bank and tax-alpha ledger (rep6).

SOURCES (Task 1c) — each realized gain or loss is counted ONCE
──────────────────────────────────────────────────────────────────────────────
  lot_close         a CLOSED portfolio.tax_lots row with realized_gain_loss — the
                    custodian's own number (keyed by the lot id); its date is the
                    closing transaction's trade_date; its character is the lot's
                    term (or acquired → trade date when the term is blank).
  fill              a rep2 fill on a SELL instruction, per lot the fill names:
                    (fill price − the lot's unit cost) × quantity. PROVISIONAL:
                    when the custodian's closed lot for the same tax lot arrives
                    (lot_close), the fill entry is CLOSED (valid_to, with the
                    reason) — the lot_close number replaces it. A fill that names
                    no lots waits for the custodian's lot (counted, not guessed).
  carryforward      an APPROVED loss_carryforwards version — its ST and LT loss
                    carried in, as two entries dated 1 January of its tax year;
                    a newer approved version closes the older version's entries.
  declared_outside  a declared outside SALE with a realized amount and term.
portfolio.transactions carry no gain of their own (rep5's finding) — they are
not a source; their gains arrive through the lots they close. With the
custodian's own harvesting on, its realized losses arrive the same way.
One row per source key (closed rows included) — :func:`ingest` is idempotent.

NETTING — the IRS order (:func:`net`)
──────────────────────────────────────────────────────────────────────────────
ST gains against ST losses (the ST carryforward is an ST loss), LT against LT
(the LT carryforward an LT loss); then a net loss on one side offsets a net
gain on the other; a net capital loss is deductible against ordinary income up
to $3,000 ($1,500 married filing separately) — taken from the short-term loss
first — and the rest carries forward KEEPING its character.

Every tax VALUE is an estimate at the subject's approved profile rates, labelled
with the rates and their as-of; no profile → "not estimated — no tax profile".
The value of a harvested loss is mostly a DEFERRAL: the replacement carries the
lower cost basis, so the benefit decays when that gain is realized later (at
the same rate it is close to the time value of the deferral; it is permanent
only through a step-up, a gift to charity, or ordinary-income offset). After-tax
PERFORMANCE needs a price history: "not estimated — needs prices".
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal

from services import tax_profiles as TP

log = logging.getLogger(__name__)

T_LB = "public.loss_bank_entries"
SOURCE_LABELS = {"lot_close": "A closed tax lot (the custodian's number)",
                 "fill": "A reported fill (provisional until the custodian's lot arrives)",
                 "carryforward": "Loss carried forward from the prior return",
                 "declared_outside": "A declared outside trade"}
CENT = Decimal("0.01")
ZERO = Decimal(0)
ORDINARY_LIMIT = Decimal(3000)
ORDINARY_LIMIT_MFS = Decimal(1500)
DECAY_NOTE = ("A harvested loss is mostly a DEFERRAL: the replacement carries the lower cost basis, so the benefit "
              "decays when that gain is realized later. It is permanent only through a step-up at death, a gift to "
              "charity, or the part used against ordinary income.")


def money(v: Decimal) -> Decimal:
    return v.quantize(CENT, rounding=ROUND_HALF_EVEN)


def _term(term_hint, acquired: date | None, sold: date) -> str | None:
    if term_hint in ("short", "long"):
        return term_hint
    if acquired is None:
        return None
    from services.rebalance_engine import long_term_from

    return "long" if sold >= long_term_from(acquired) else "short"


# ── Netting (pure) ──────────────────────────────────────────────────────────
def net(*, st_gains: Decimal, st_losses: Decimal, lt_gains: Decimal, lt_losses: Decimal, cf_st: Decimal = ZERO,
        cf_lt: Decimal = ZERO, filing_status: str | None = None) -> dict:
    """Losses and carryforwards as POSITIVE magnitudes. Returns every step."""
    net_st = st_gains - st_losses - cf_st
    net_lt = lt_gains - lt_losses - cf_lt
    # cross-net
    if net_st < 0 < net_lt:
        cross = min(-net_st, net_lt)
        after_st, after_lt = net_st + cross, net_lt - cross
    elif net_lt < 0 < net_st:
        cross = min(-net_lt, net_st)
        after_st, after_lt = net_st - cross, net_lt + cross
    else:
        cross = ZERO
        after_st, after_lt = net_st, net_lt
    limit = ORDINARY_LIMIT_MFS if filing_status == "married_filing_separately" else ORDINARY_LIMIT
    st_loss_left = -after_st if after_st < 0 else ZERO
    lt_loss_left = -after_lt if after_lt < 0 else ZERO
    deduct = min(limit, st_loss_left + lt_loss_left)
    d_st = min(deduct, st_loss_left)
    d_lt = deduct - d_st
    return {"net_st": net_st, "net_lt": net_lt, "cross_netted": cross,
            "st_after_cross": after_st, "lt_after_cross": after_lt,
            "net_st_gain": after_st if after_st > 0 else ZERO, "net_lt_gain": after_lt if after_lt > 0 else ZERO,
            "ordinary_limit": limit, "ordinary_offset": deduct, "ordinary_offset_from_st": d_st,
            "ordinary_offset_from_lt": d_lt, "carry_st": st_loss_left - d_st, "carry_lt": lt_loss_left - d_lt}


def _strs(d: dict) -> dict:
    return {k: (str(money(v)) if isinstance(v, Decimal) else v) for k, v in d.items()}


# ── Ingest (idempotent) ─────────────────────────────────────────────────────
_INSERT = f"""INSERT INTO {T_LB} (org_id, subject_type, subject_id, tax_year, account_id, trade_date, character, amount,
                   source, source_id, source_key, tax_lot_id, global_security_id, detail)
              VALUES ($1::uuid, $2, $3::uuid, $4, $5::uuid, $6, $7, $8, $9, $10::uuid, $11, $12::uuid, $13::uuid,
                      $14::jsonb)
              ON CONFLICT (org_id, source, source_key) WHERE system_to IS NULL DO NOTHING RETURNING id"""


async def ingest(pool, *, org_id, today: date | None = None) -> dict:
    """Every source into the bank for one org (the caller set its RLS context).
    Re-running adds nothing."""
    today = today or date.today()
    out = {"added": {k: 0 for k in SOURCE_LABELS}, "superseded": 0, "waiting": {"no_closing_date": 0,
           "no_term": 0, "fill_without_lots": 0, "fill_lot_cost_unknown": 0}}
    async with pool.acquire() as conn:
        async with conn.transaction():
            # 1. closed lots — the custodian's numbers
            for r in await conn.fetch(
                    """SELECT tl.id, tl.realized_gain_loss, tl.term, tl.acquired_date, x.trade_date, p.account_id,
                              a.global_security_id, ac.household_id, ac.primary_entity_id
                       FROM portfolio.tax_lots tl
                       JOIN portfolio.positions p ON p.id = tl.position_id AND p.system_to IS NULL
                       JOIN portfolio.assets a ON a.id = p.asset_id AND a.system_to IS NULL
                       JOIN public.accounts ac ON ac.id = p.account_id AND ac.system_to IS NULL AND ac.valid_to IS NULL
                       LEFT JOIN portfolio.transactions x ON x.id = tl.closing_transaction_id AND x.system_to IS NULL
                       WHERE tl.org_id = $1::uuid AND tl.status = 'closed' AND tl.realized_gain_loss IS NOT NULL
                         AND tl.valid_to IS NULL AND tl.system_to IS NULL""", str(org_id)):
                if r["trade_date"] is None:
                    out["waiting"]["no_closing_date"] += 1
                    continue
                ch = _term(r["term"], r["acquired_date"], r["trade_date"])
                if ch is None:
                    out["waiting"]["no_term"] += 1
                    continue
                st, sid = TP.subject_of(dict(r))
                got = await conn.fetchval(_INSERT, str(org_id), st, sid, r["trade_date"].year, r["account_id"],
                                          r["trade_date"], ch, money(Decimal(str(r["realized_gain_loss"]))),
                                          "lot_close", r["id"], str(r["id"]), r["id"], r["global_security_id"],
                                          TP._dumps({"term_from": "lot" if r["term"] else "dates"}))
                if got:
                    out["added"]["lot_close"] += 1
                # the custodian's lot replaces any provisional fill estimate for it
                n = await conn.fetch(
                    f"UPDATE {T_LB} SET valid_to = now(), superseded_reason = $3 WHERE org_id = $1::uuid "
                    "AND source = 'fill' AND tax_lot_id = $2 AND valid_to IS NULL AND system_to IS NULL RETURNING id",
                    str(org_id), r["id"], f"the custodian's closed lot {r['id']} replaces this provisional fill estimate")
                out["superseded"] += len(n)
            closed_lots = {r["tax_lot_id"] for r in await conn.fetch(
                f"SELECT tax_lot_id FROM {T_LB} WHERE org_id = $1::uuid AND source = 'lot_close' AND valid_to IS NULL "
                "AND system_to IS NULL", str(org_id))}
            # 2. fills on sell instructions, per named lot (provisional)
            for f in await conn.fetch(
                    """SELECT f.id, f.price, f.filled_at, f.lots, i.account_id, i.global_security_id, ac.household_id,
                              ac.primary_entity_id
                       FROM public.execution_fills f
                       JOIN public.execution_instructions i ON i.id = f.instruction_id AND i.system_to IS NULL
                       JOIN public.accounts ac ON ac.id = i.account_id AND ac.system_to IS NULL AND ac.valid_to IS NULL
                       WHERE f.org_id = $1::uuid AND i.side = 'sell' AND f.valid_to IS NULL AND f.system_to IS NULL""",
                    str(org_id)):
                lots = TP._loads(f["lots"]) or []
                if not lots:
                    out["waiting"]["fill_without_lots"] += 1
                    continue
                d = f["filled_at"].date()
                st, sid = TP.subject_of(dict(f))
                for x in lots:
                    lot = await conn.fetchrow(
                        "SELECT id, quantity, adjusted_cost, original_cost, term, acquired_date FROM portfolio.tax_lots "
                        "WHERE id = $1::uuid AND system_to IS NULL ORDER BY valid_to NULLS FIRST LIMIT 1",
                        str(x["tax_lot_id"]))
                    if lot is None or lot["id"] in closed_lots:
                        continue
                    cost = lot["adjusted_cost"] if lot["adjusted_cost"] is not None else lot["original_cost"]
                    if cost is None or not lot["quantity"]:
                        out["waiting"]["fill_lot_cost_unknown"] += 1
                        continue
                    ch = _term(None, lot["acquired_date"], d) or _term(lot["term"], None, d)
                    if ch is None:
                        out["waiting"]["no_term"] += 1
                        continue
                    q = Decimal(str(x["quantity"]))
                    amount = money(q * (Decimal(str(f["price"])) - Decimal(str(cost)) / Decimal(str(lot["quantity"]))))
                    got = await conn.fetchval(_INSERT, str(org_id), st, sid, d.year, f["account_id"], d, ch, amount,
                                              "fill", f["id"], f"{f['id']}:{lot['id']}", lot["id"],
                                              f["global_security_id"],
                                              TP._dumps({"quantity": str(q), "fill_price": str(f["price"]),
                                                         "provisional": True}))
                    if got:
                        out["added"]["fill"] += 1
            # 3. approved carryforwards (a newer approved version closes the older one's entries)
            live_cf = {str(r["id"]) for r in await conn.fetch(
                f"SELECT id FROM {TP.T_CF} WHERE org_id = $1::uuid AND status = 'approved' AND valid_to IS NULL "
                "AND system_to IS NULL", str(org_id))}
            n = await conn.fetch(
                f"UPDATE {T_LB} SET valid_to = now(), superseded_reason = 'the carryforward was replaced by a newer "
                "approved version' WHERE org_id = $1::uuid AND source = 'carryforward' AND valid_to IS NULL "
                "AND system_to IS NULL AND NOT (source_id::text = ANY($2::text[])) RETURNING id",
                str(org_id), sorted(live_cf))
            out["superseded"] += len(n)
            for r in await conn.fetch(f"SELECT * FROM {TP.T_CF} WHERE org_id = $1::uuid AND status = 'approved' "
                                      "AND valid_to IS NULL AND system_to IS NULL", str(org_id)):
                for ch, amt in (("short", r["st_amount"]), ("long", r["lt_amount"])):
                    if not amt:
                        continue
                    got = await conn.fetchval(_INSERT, str(org_id), r["subject_type"], r["subject_id"], r["tax_year"],
                                              None, date(r["tax_year"], 1, 1), ch, -money(Decimal(str(amt))),
                                              "carryforward", r["id"], f"{r['id']}:{ch}", None, None,
                                              TP._dumps({"source_document": r["source_document"],
                                                         "version_no": r["version_no"]}))
                    if got:
                        out["added"]["carryforward"] += 1
            # 4. declared outside sales with a realized amount (a withdrawn / corrected one closes its entry)
            live_decl = {str(r["id"]) for r in await conn.fetch(
                f"SELECT id FROM {TP.T_DECL} WHERE org_id = $1::uuid AND valid_to IS NULL AND system_to IS NULL",
                str(org_id))}
            n = await conn.fetch(
                f"UPDATE {T_LB} SET valid_to = now(), superseded_reason = 'the declared trade was corrected or "
                "withdrawn' WHERE org_id = $1::uuid AND source = 'declared_outside' AND valid_to IS NULL "
                "AND system_to IS NULL AND NOT (source_id::text = ANY($2::text[])) RETURNING id",
                str(org_id), sorted(live_decl))
            out["superseded"] += len(n)
            for r in await conn.fetch(
                    f"SELECT * FROM {TP.T_DECL} WHERE org_id = $1::uuid AND side = 'sell' AND realized_amount IS NOT NULL "
                    "AND valid_to IS NULL AND system_to IS NULL", str(org_id)):
                got = await conn.fetchval(_INSERT, str(org_id), r["subject_type"], r["subject_id"], r["trade_date"].year,
                                          None, r["trade_date"], r["term"], money(Decimal(str(r["realized_amount"]))),
                                          "declared_outside", r["id"], str(r["id"]), None, r["global_security_id"],
                                          TP._dumps({"account_description": r["account_description"]}))
                if got:
                    out["added"]["declared_outside"] += 1
    return out


# ── Reading ─────────────────────────────────────────────────────────────────
async def entries(conn, org_id, subject_type: str, subject_id, tax_year: int, *, account_id=None) -> list[dict]:
    args = [str(org_id), subject_type, str(subject_id), int(tax_year)]
    sql = (f"SELECT * FROM {T_LB} WHERE org_id = $1::uuid AND subject_type = $2 AND subject_id = $3::uuid "
           "AND tax_year = $4 AND valid_to IS NULL AND system_to IS NULL")
    if account_id:
        args.append(str(account_id))
        sql += " AND (account_id = $5::uuid)"
    return [dict(r) for r in await conn.fetch(sql + " ORDER BY trade_date, source, source_key", *args)]


def totals(rows: list[dict]) -> dict:
    t = {"st_gains": ZERO, "st_losses": ZERO, "lt_gains": ZERO, "lt_losses": ZERO, "cf_st": ZERO, "cf_lt": ZERO}
    for r in rows:
        a = Decimal(str(r["amount"]))
        if r["source"] == "carryforward":
            t["cf_st" if r["character"] == "short" else "cf_lt"] += -a
        elif r["character"] == "short":
            t["st_gains" if a >= 0 else "st_losses"] += a if a >= 0 else -a
        else:
            t["lt_gains" if a >= 0 else "lt_losses"] += a if a >= 0 else -a
    return t


async def harvested(conn, org_id, account_ids: list[str], tax_year: int) -> dict:
    """Losses HARVESTED (candidates on harvest proposals a person approved) by
    character, with their estimated values as recorded at the time."""
    if not account_ids:
        return {"st": ZERO, "lt": ZERO, "est_value": ZERO, "estimated": 0, "not_estimated": 0, "rows": []}
    rows = await conn.fetch(
        """SELECT c.*, p.status AS proposal_status FROM public.harvest_candidates c
           JOIN public.rebalance_proposals p ON p.id = c.proposal_id AND p.system_to IS NULL
           JOIN public.harvest_runs r ON r.id = c.run_id
           WHERE c.org_id = $1::uuid AND c.account_id = ANY($2::uuid[]) AND c.kind = 'harvest'
             AND c.outcome = 'proposed' AND p.status IN ('approved', 'executed')
             AND extract(year FROM r.run_date) = $3 AND c.valid_to IS NULL AND c.system_to IS NULL""",
        str(org_id), account_ids, int(tax_year))
    out = {"st": ZERO, "lt": ZERO, "est_value": ZERO, "estimated": 0, "not_estimated": 0, "rows": []}
    for r in rows:
        loss = -Decimal(str(r["unrealized"]))
        out["st" if r["term"] == "short" else "lt"] += loss
        if r["est_tax_value"] is not None:
            out["est_value"] += Decimal(str(r["est_tax_value"]))
            out["estimated"] += 1
        else:
            out["not_estimated"] += 1
        out["rows"].append({"candidate_id": str(r["id"]), "account_id": str(r["account_id"]),
                            "proposal_id": str(r["proposal_id"]), "proposal_status": r["proposal_status"],
                            "security_id": str(r["global_security_id"]), "loss": str(money(loss)), "term": r["term"],
                            "est_tax_value": TP._num(r["est_tax_value"]), "value_note": r["value_note"]})
    return out


async def summary(conn, org_id, subject_type: str, subject_id, tax_year: int, *, account_ids: list[str] | None = None
                  ) -> dict:
    """The ledger for one subject and year: totals, the IRS netting, the
    carryforward in and out, harvested losses and their estimated value."""
    rows = await entries(conn, org_id, subject_type, subject_id, tax_year)
    t = totals(rows)
    profile = await TP.approved_profile(conn, org_id, subject_type, subject_id, tax_year)
    rates = TP.combined_rates(profile)
    n = net(**t, filing_status=profile["filing_status"] if profile else None)
    if account_ids is None:
        account_ids = [str(r["id"]) for r in await conn.fetch(
            "SELECT id FROM public.accounts WHERE org_id = $1::uuid AND system_to IS NULL AND valid_to IS NULL AND "
            + ("household_id = $2::uuid" if subject_type == "household" else
               "household_id IS NULL AND primary_entity_id = $2::uuid"), str(org_id), str(subject_id))]
    hv = await harvested(conn, org_id, account_ids, tax_year)
    if rates:
        value = {"estimated": True, "label": TP.ESTIMATE, "rates": TP.rates_out(rates),
                 "realized_losses_value": str(money(t["st_losses"] * rates["st"] + t["lt_losses"] * rates["lt"])),
                 "harvested_value": str(money(hv["st"] * rates["st"] + hv["lt"] * rates["lt"])),
                 "net_gain_tax": str(money(n["net_st_gain"] * rates["st"] + n["net_lt_gain"] * rates["lt"])),
                 "note": DECAY_NOTE}
    else:
        value = {"estimated": False, "label": TP.NO_PROFILE, "note": DECAY_NOTE}
    return {"subject_type": subject_type, "subject_id": str(subject_id), "tax_year": int(tax_year),
            "totals": _strs(t), "netting": _strs(n),
            "carryforward_in": {"st": str(money(t["cf_st"])), "lt": str(money(t["cf_lt"]))},
            "carryforward_out": {"st": str(money(n["carry_st"])), "lt": str(money(n["carry_lt"])),
                                 "into_year": int(tax_year) + 1},
            "harvested": {"st": str(money(hv["st"])), "lt": str(money(hv["lt"])), "rows": hv["rows"],
                          "recorded_est_value": str(money(hv["est_value"])), "label": TP.ESTIMATE},
            "tax_value": value, "filing_status": profile["filing_status"] if profile else None,
            "after_tax_performance": TP.NEEDS_PRICES, "tracking_error_cost": TP.NEEDS_PRICES,
            "entries": [entry_out(r) for r in rows]}


def entry_out(r: dict) -> dict:
    return {"id": str(r["id"]), "tax_year": r["tax_year"], "account_id": str(r["account_id"]) if r["account_id"] else None,
            "trade_date": r["trade_date"].isoformat(), "character": r["character"], "amount": str(r["amount"]),
            "source": r["source"], "source_id": str(r["source_id"]), "source_key": r["source_key"],
            "tax_lot_id": str(r["tax_lot_id"]) if r["tax_lot_id"] else None,
            "global_security_id": str(r["global_security_id"]) if r["global_security_id"] else None,
            "detail": TP._loads(r["detail"]) or {}}


def vocabularies() -> dict:
    return {"ledger_sources": [{"key": k, "label": v} for k, v in SOURCE_LABELS.items()],
            "characters": [{"key": "short", "label": "Short-term"}, {"key": "long", "label": "Long-term"}]}

