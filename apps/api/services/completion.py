"""REP10 — completion portfolios and overlap consolidation.

COMPLETION (a MANAGED account's replica built AROUND what the household holds elsewhere)
──────────────────────────────────────────────────────────────────────────────
Settings per account (public.completion_settings, valid axis): count_elsewhere
on / off, which authorities count (read_only and advise by default; managed
is opt-in — two managed completions would net each other), and a stricter
stale threshold (else rep7's per-account one).

The rule — :func:`complete` (pure, exact):
  A  = the account's own value (its valued holdings + cash);
  E_k = the household's exposure to security k held in the COUNTED other
        accounts — the X-ray's per-account look-through (a fund held elsewhere
        counts by its constituents);
  H  = A + every counted account's value;
  the account's dollars in k: D_k = T_k × H − E_k  (T = the target after
        exclusions and tilts). NEVER NEGATIVE: a k already held above
        T_k × H elsewhere gets 0 here and its excess is reported "over target
        elsewhere — not traded";
  weights: w_k = D_k ÷ A (numeric(14,10)); the cash target takes the rest of
        the base total. When the counted accounts hold more cash than the
        model asks (the rest would go negative), the securities scale down pro
        rata so cash is zero, and the shortfall is stated.
So the HOUSEHOLD's dollars in each security equal T_k × H (within the stated
rounding, 1e-10 of the account) wherever nothing is clipped. Exposure the
security master cannot resolve is counted in H, never netted (stated).

Counted held-away data must be fresh: an account whose data is older than the
threshold counts only once an adviser has confirmed that as-of (rep7's
stale_confirmations, purpose 'general'). Until then completion is NOT applied
at all — the plain replica target stands and the reason says so. Held-away
and read-only accounts are never traded (rep7b).

CONSOLIDATION — :func:`consolidation`
──────────────────────────────────────────────────────────────────────────────
From the household X-ray: every looked-through fund whose overlap with a
followed replica (Σ min(fund weight, model weight)) or with another held fund
is at least replication.consolidation.overlap_threshold (default 25%). Per
holding: managed + following a replica → a rep5 proposal (the fund is outside
the target, so the engine sells it under the lot method, gain budget and
wash-sale control, and buys the replica); managed + no model → a join
proposal (rep7's open_join); advise → a one-off advice proposal whose
instructions print as the advice letter (rep7b); read_only → listed, never
traded. Transition tax and break-even are rep7's, shown as estimates.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from services import rebalance_engine as E
from services import rep10_common as C

T_SET = "public.completion_settings"
AUTHORITIES = ("read_only", "advise", "managed")
DEFAULT_COUNT = ("read_only", "advise")
OVERLAP_SETTING = "replication.consolidation.overlap_threshold"
DEFAULT_OVERLAP = Decimal("0.25")
OVER_ELSEWHERE = "over target elsewhere — not traded"
ACTION_LABELS = {"rebalance": "Rebalance into the replica (rep5 proposal)",
                 "join": "Join the replica (a join proposal)",
                 "one_off": "Advice letter (one-off advice proposal)",
                 "listed": "Read-only — listed, never traded"}


# ═══ Pure ═══════════════════════════════════════════════════════════════════
def complete(target: dict[str, Decimal], account_value: Decimal, elsewhere: dict[str, Decimal],
             elsewhere_value: Decimal) -> dict:
    """``target`` {key | 'cash': weight}; ``elsewhere`` {key: dollars held in counted accounts}. Exact."""
    A = C.dec(account_value)
    H = A + C.dec(elsewhere_value)
    base_total = sum(target.values(), C.ZERO)
    if A <= 0:
        raise C.Rep10Conflict("the account has no value to complete with")
    dollars, excess = {}, []
    for k, t in target.items():
        if k == E.CASH:
            continue
        d = t * H - elsewhere.get(k, C.ZERO)
        if d < 0:
            excess.append({"key": k, "target_dollars": str(C.money(t * H)),
                           "held_elsewhere": str(C.money(elsewhere.get(k, C.ZERO))),
                           "excess": str(C.money(-d)), "note": OVER_ELSEWHERE})
            d = C.ZERO
        dollars[k] = d
    for k, v in elsewhere.items():          # held elsewhere, not in the target at all → all excess
        if k != E.CASH and k not in target and v > 0:
            excess.append({"key": k, "target_dollars": "0.00", "held_elsewhere": str(C.money(v)),
                           "excess": str(C.money(v)), "note": OVER_ELSEWHERE})
    w = {k: E.qw(v / A) for k, v in dollars.items()}
    notes = []
    sec = sum(w.values(), C.ZERO)
    if sec > base_total:
        scaled = E.allocate([(k, v, i) for i, (k, v) in enumerate(sorted(w.items())) if v > 0], base_total)
        notes.append(f"the counted accounts hold less of the model's securities than the account can fill: the "
                     f"securities need {E.pct(sec)} of this account, so they are scaled to {E.pct(base_total)} and "
                     "cash is zero")
        w = {k: scaled.get(k, C.ZERO) for k in w}
        sec = base_total
    w[E.CASH] = base_total - sec
    return {"weights": w, "excess": excess, "notes": notes, "household_value": H, "account_value": A,
            "dollars": dollars}


# ═══ Settings ═══════════════════════════════════════════════════════════════
async def settings_for(conn, org_id, account_id) -> dict:
    row = await conn.fetchrow(f"SELECT * FROM {T_SET} WHERE org_id = $1::uuid AND account_id = $2::uuid AND "
                              "valid_to IS NULL AND system_to IS NULL", str(org_id), str(account_id))
    if row is None:
        return {"count_elsewhere": False, "count_authorities": list(DEFAULT_COUNT), "stale_after_days": None,
                "source": "default", "id": None}
    return {"count_elsewhere": row["count_elsewhere"], "count_authorities": list(row["count_authorities"]),
            "stale_after_days": row["stale_after_days"], "source": "account", "id": str(row["id"]),
            "reason": row["reason"], "set_by": str(row["set_by"]), "valid_from": C.iso(row["valid_from"])}


async def set_settings(pool, *, org_id, actor_id, account_id, count_elsewhere: bool, count_authorities: list[str],
                       stale_after_days: int | None, reason: str) -> dict:
    from services import account_models as AM
    from services import held_away as HA

    if not (reason or "").strip():
        raise C.Rep10Error("say why the completion settings change")
    auth = sorted(set(count_authorities or []))
    if not auth or any(a not in AUTHORITIES for a in auth):
        raise C.Rep10Error(f"count authorities are some of {list(AUTHORITIES)}")
    if stale_after_days is not None and not (1 <= int(stale_after_days) <= 366):
        raise C.Rep10Error("the stale threshold is 1 to 366 days")
    async with pool.acquire() as conn:
        actor = await C.human(conn, actor_id, org_id)
        account = await AM.load_account(conn, org_id, C.uid(account_id, "account"))
        HA.refuse(account, "completion settings", C.Rep10Conflict, path="settings")
        async with conn.transaction():
            await conn.execute(f"UPDATE {T_SET} SET valid_to = now() WHERE account_id = $1::uuid AND valid_to IS NULL "
                               "AND system_to IS NULL", str(account["id"]))
            row = await conn.fetchrow(
                f"INSERT INTO {T_SET} (org_id, account_id, count_elsewhere, count_authorities, stale_after_days, "
                "reason, set_by) VALUES ($1::uuid, $2::uuid, $3, $4::text[], $5, $6, $7::uuid) RETURNING id",
                str(org_id), str(account["id"]), bool(count_elsewhere), auth,
                int(stale_after_days) if stale_after_days is not None else None, reason.strip(), str(actor["id"]))
            await C.audit(conn, org_id, actor["id"], "completion_settings", "completion_settings", row["id"],
                          {"account_id": str(account["id"]), "count_elsewhere": bool(count_elsewhere),
                           "count_authorities": auth, "stale_after_days": stale_after_days})
        return await settings_for(conn, org_id, account["id"])


# ═══ What the household holds elsewhere ═════════════════════════════════════
async def elsewhere(conn, org_id, account: dict, settings: dict, today: date) -> dict:
    """The counted accounts' exposure by security (dollars), their value, and each one's freshness."""
    from services import held_away as HA
    from services import xray as XR

    if not account.get("household_id"):
        return {"available": False, "reason": "the account is in no household — nothing to complete around"}
    x = await XR.compute(conn, org_id, account["household_id"], today=today)
    counted = {a["id"]: a for a in x["accounts"] if a["id"] != str(account["id"])
               and (a.get("authority") or "read_only") in settings["count_authorities"]}
    exp: dict[str, Decimal] = {}
    unresolved: dict[str, Decimal] = {}
    value: dict[str, Decimal] = {}
    for c in x["constituents"]:
        if c["account_id"] not in counted:
            continue
        amt = Decimal(c["exposure"])
        value[c["account_id"]] = value.get(c["account_id"], C.ZERO) + amt
        key = "cash" if c["label"] == "Cash" and c["security_id"] is None else c["security_id"]
        if key is None:
            unresolved[c["label"]] = unresolved.get(c["label"], C.ZERO) + amt
        else:
            exp[key] = exp.get(key, C.ZERO) + amt
    fresh = []
    for aid, a in counted.items():
        if not a.get("held_away"):
            fresh.append({"account_id": aid, "account": a["account_number_masked"], "authority": a.get("authority"),
                          "held_away": False, "stale": False, "confirmed": None, "blocking": False})
            continue
        as_of = await HA.account_as_of(conn, org_id, aid)
        days, origin = await HA.stale_days_for(conn, org_id, aid)
        if settings.get("stale_after_days"):
            days, origin = min(days, int(settings["stale_after_days"])), "completion_setting"
        stale = HA.is_stale(as_of, today, days)
        conf = await HA._live_confirmation(conn, org_id, aid, purpose="general")
        ok = HA.confirmation_holds(conf, as_of, today)
        fresh.append({"account_id": aid, "account": a["account_number_masked"], "authority": a.get("authority"),
                      "held_away": True, "as_of": C.iso(as_of), "stale_after_days": days, "threshold_from": origin,
                      "stale": stale, "confirmed": ok, "blocking": stale and not ok,
                      "confirmation": HA.confirmation_out(conf) if conf else None})
    return {"available": True, "household": x["household"], "exposure": exp, "unresolved": unresolved,
            "accounts": fresh, "value": sum(value.values(), C.ZERO), "by_account_value": value}


async def for_target(conn, org_id, account: dict, target: dict, labels: dict, today: date) -> dict | None:
    """The completion applied to ``target`` (rebalance_proposals.target_for); None when it is off."""
    from services import account_models as AM

    s = await settings_for(conn, org_id, account["id"])
    if not s["count_elsewhere"]:
        return None
    if account.get("authority") not in (None, "managed"):
        return {"applied": False, "reasons": ["completion applies to a managed account only"], "settings": s}
    el = await elsewhere(conn, org_id, account, s, today)
    if not el["available"]:
        return {"applied": False, "reasons": [el["reason"]], "settings": s}
    blocking = [f for f in el["accounts"] if f["blocking"]]
    if blocking:
        return {"applied": False, "settings": s, "elsewhere": el, "needs_confirmation": blocking,
                "reasons": [f"completion not applied — held-away data for {f['account']} is as of {f['as_of'] or 'never'}"
                            f" (older than {f['stale_after_days']} day(s)): an adviser must confirm it" for f in blocking]}
    hold = await AM.load_holdings(conn, org_id, account["id"])
    A = sum((h.market_value for h in hold["holdings"] if h.market_value is not None), C.ZERO) + hold["cash"]
    res = complete(target, A, el["exposure"], el["value"])
    for e in res["excess"]:
        e["label"] = labels.get(e["key"], e["key"])
    return {"applied": True, "settings": s, "elsewhere": el, "result": res}


async def preview(conn, org_id, account_id, *, today: date | None = None) -> dict:
    """The account's completion: settings, what is counted, the household check, the excess — no writes."""
    from services import account_models as AM
    from services import rebalance_proposals as RP

    today = today or date.today()
    account = await AM.load_account(conn, org_id, C.uid(account_id, "account"))
    s = await settings_for(conn, org_id, account["id"])
    out = {"account": {"id": str(account["id"]), "account_number_masked": account["account_number_masked"],
                       "authority": account.get("authority"), "custody": account.get("custody"),
                       "household_id": str(account["household_id"]) if account.get("household_id") else None},
           "settings": s, "rule": __doc__.split("The rule — ")[1].split("Counted held-away")[0].strip()}
    link = await AM.active_link(conn, account["id"])
    if link is None:
        return {**out, "applied": False, "reasons": ["the account does not follow a model"]}
    model = await AM.model_brief(conn, org_id, link["model_id"])
    base = await RP.target_for(conn, org_id, link, model["live_version_id"], adjust=False)
    target = base["target"]
    from services import tilts as TL

    t = await TL.for_target(conn, org_id, account["id"], model["live_version_id"], target, base["labels"], today)
    if t and t["applied"]:
        target = t["result"]["final"]
    probe = {**s, "count_elsewhere": True}
    if account.get("authority") not in (None, "managed"):
        return {**out, "applied": False, "reasons": ["completion applies to a managed account only — this account is "
                                                     f"{account.get('authority')}"]}
    el = await elsewhere(conn, org_id, account, probe, today)
    if not el["available"]:
        return {**out, "applied": False, "reasons": [el["reason"]]}
    hold = await AM.load_holdings(conn, org_id, account["id"])
    A = sum((h.market_value for h in hold["holdings"] if h.market_value is not None), C.ZERO) + hold["cash"]
    res = complete(target, A, el["exposure"], el["value"])
    H = res["household_value"]
    check = []
    for k, t_ in sorted(target.items()):
        if k == E.CASH:
            continue
        own = res["weights"].get(k, C.ZERO) * A
        hh = own + el["exposure"].get(k, C.ZERO)
        check.append({"key": k, "label": base["labels"].get(k, k), "model_weight": str(t_),
                      "account_weight": str(res["weights"].get(k, C.ZERO)),
                      "household_dollars": str(C.money(hh)), "household_weight": str(E.qw(hh / H)) if H else None,
                      "held_elsewhere": str(C.money(el["exposure"].get(k, C.ZERO)))})
    for e in res["excess"]:
        e["label"] = base["labels"].get(e["key"], e["key"])
    blocking = [f for f in el["accounts"] if f["blocking"]]
    return {**out, "applied": bool(s["count_elsewhere"]) and not blocking, "would_apply": not blocking,
            "accounts_counted": el["accounts"], "needs_confirmation": blocking,
            "account_value": str(C.money(A)), "household_value": str(C.money(H)),
            "weights": [{"key": k, "label": "Cash" if k == E.CASH else base["labels"].get(k, k), "weight": str(v)}
                        for k, v in sorted(res["weights"].items(), key=lambda kv: (kv[0] == E.CASH, -kv[1]))],
            "household_check": check, "excess": res["excess"], "notes": res["notes"],
            "unresolved_elsewhere": {k: str(C.money(v)) for k, v in el["unresolved"].items()},
            "never_traded": [f["account"] for f in el["accounts"] if f["authority"] in ("read_only", "advise")]}


async def confirm_request(pool, *, org_id, actor_id, account_id) -> dict:
    """Ask for the adviser's confirmation of a counted held-away account's data (rep7's task, purpose 'general')."""
    from services import held_away as HA

    row = await HA.request_confirmation(pool, org_id=org_id, account_id=C.uid(account_id, "account"),
                                        actor_id=actor_id, purpose="general")
    return HA.confirmation_out(row)


# ═══ Consolidation ══════════════════════════════════════════════════════════
def _min_sum(a: dict[str, Decimal], b: dict[str, Decimal]) -> Decimal:
    return sum((min(a[k], b[k]) for k in set(a) & set(b)), C.ZERO)


async def consolidation(conn, org_id, household_id, *, today: date | None = None) -> dict:
    from services import account_models as AM
    from services import xray as XR

    today = today or date.today()
    x = await XR.compute(conn, org_id, C.uid(household_id, "household"), today=today)
    thr = await C.setting(conn, org_id, OVERLAP_SETTING, DEFAULT_OVERLAP, lo=Decimal("0.0000000001"), hi=Decimal(1))
    accts = {a["id"]: a for a in x["accounts"]}
    rows = await XR.household_accounts(conn, org_id, x["household"]["id"])
    replicas = await XR.followed_replicas(conn, org_id, rows)
    fund_w: dict[str, dict[str, Decimal]] = {}
    for c in x["constituents"]:
        if c.get("look_through") and c["fund_security_id"] and c["security_id"]:
            fw = fund_w.setdefault(c["fund_security_id"], {})
            fw.setdefault(c["security_id"], Decimal(c["weight"]))      # the fund's own file weight
    fund_pairs = {}
    for o in x["overlap"]:
        for a, b in ((o["fund_a"], o["fund_b"]), (o["fund_b"], o["fund_a"])):
            if Decimal(o["overlap_weight"]) > Decimal(fund_pairs.get(a, {}).get("overlap_weight", "0")):
                fund_pairs[a] = {"fund": b, "label": o["fund_b_label"] if a == o["fund_a"] else o["fund_a_label"],
                                 "overlap_weight": o["overlap_weight"]}
    lines = []
    for f in x["funds"]:
        fw = fund_w.get(f["fund_security_id"])
        if not fw:
            continue
        best = None
        for m in replicas:
            ov = E.qw(_min_sum(fw, m["weights"]))
            if best is None or ov > best["overlap_weight"]:
                best = {"model_id": m["model_id"], "name": m["name"], "overlap_weight": ov}
        pair = fund_pairs.get(f["fund_security_id"])
        hits = (best is not None and best["overlap_weight"] >= thr) or (pair and Decimal(pair["overlap_weight"]) >= thr)
        if not hits:
            continue
        a = accts.get(f["account_id"], {})
        auth = a.get("authority") or "read_only"
        link = await AM.active_link(conn, f["account_id"]) if auth == "managed" else None
        action = ("rebalance" if link else "join") if auth == "managed" else ("one_off" if auth == "advise"
                                                                               else "listed")
        lines.append({"account_id": f["account_id"], "account": f["account"], "authority": auth,
                      "custody": a.get("custody"), "held_away": f["held_away"],
                      "fund_security_id": f["fund_security_id"], "label": f["label"],
                      "market_value": f["market_value"],
                      "replica_overlap": ({**best, "overlap_weight": str(best["overlap_weight"])} if best else None),
                      "fund_overlap": pair, "action": action, "action_label": ACTION_LABELS[action],
                      "transition_tax": f.get("transition_tax"), "break_even": f.get("break_even"),
                      "embedded_gain": f.get("embedded_gain"), "fee": f.get("fee"), "model": f.get("model"),
                      "status_lines": f.get("status_lines")})
    return {"household": x["household"], "as_of": x["as_of"], "threshold": str(thr), "setting": OVERLAP_SETTING,
            "lines": lines, "overlap": x["overlap"], "replica_overlap": x["replica_overlap"], "label": C.ESTIMATE}


async def consolidate(pool, *, org_id, actor_id, household_id, account_id, fund_security_id,
                      executor_type: str = "member") -> dict:
    """Act on one listed holding by its account's authority (never a read-only one)."""
    from services import rebalance_proposals as RP
    from services import xray as XR

    async with pool.acquire() as conn:
        await C.human(conn, actor_id, org_id)
        view = await consolidation(conn, org_id, household_id)
    line = next((ln for ln in view["lines"] if ln["account_id"] == C.uid(account_id, "account")
                 and ln["fund_security_id"] == C.uid(fund_security_id, "fund")), None)
    if line is None:
        raise C.Rep10NotFound("that holding is not an overlapping fund in this household's consolidation list")
    if line["action"] == "listed":
        raise C.Rep10Conflict(f"{line['account']} is read-only — it is listed, never traded")
    if line["action"] == "rebalance":
        p = await RP.generate(pool, org_id=org_id, account_id=line["account_id"], trigger="manual", maker_kind="user",
                              maker_id=actor_id, trigger_ref={"consolidation": {"household_id": view["household"]["id"],
                                                                                "fund_security_id": line[
                                                                                    "fund_security_id"]}})
        return {"action": "rebalance", "proposal_id": str(p["id"]), "line": line}
    if line["action"] == "join":
        r = await XR.open_join(pool, org_id=org_id, actor_id=actor_id, household_id=view["household"]["id"],
                               account_id=line["account_id"], fund_security_id=line["fund_security_id"])
        return {"action": "join", "result": C.clean(r), "line": line}
    r = await XR.open_one_off(pool, org_id=org_id, actor_id=actor_id, household_id=view["household"]["id"],
                              account_id=line["account_id"], fund_security_id=line["fund_security_id"],
                              executor_type=executor_type)
    return {"action": "one_off", "result": C.clean(r), "line": line}


def vocabularies() -> dict:
    return {"authorities": [{"key": "read_only", "label": "Read-only"}, {"key": "advise", "label": "Advise"},
                            {"key": "managed", "label": "Managed"}],
            "consolidation_actions": [{"key": k, "label": v} for k, v in ACTION_LABELS.items()],
            "over_elsewhere_label": OVER_ELSEWHERE}
