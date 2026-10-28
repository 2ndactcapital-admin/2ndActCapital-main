"""The rank walk — THE read path for every security-data reader.

    get(conn, org_id, data_type, subject, *, as_of, purpose, account_ids, max_age_days)
    get_bulk(... subjects ...) -> {subject: Result}
    history(conn, org_id, data_type, subject, *, start, end, purpose) -> Result (value = [(date, Decimal)])

For each subject, providers are tried in rank order (the org's override for
the type, else the platform default). A provider is SKIPPED, with the reason
recorded in ``tried``, when it is not active, needs an entitlement the org
lacks, has no code module or capability for the type, or its licence (or the
value's own licence) does not allow the purpose; a value older than the
staleness limit is skipped too ("stale") — the walk continues. The first
allowed, fresh value wins. Nothing → found=False with ``missing`` naming the
data type and every source's reason. A price is never invented.

A winner is flagged 'disagreement' when another allowed source holds a value
for the SAME date that differs by more than security_data.disagreement_pct.
Readers never name a provider; they get provider_key / name back as data.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from services.security_data import quality as Q
from services.security_data import registry as R
from services.security_data import settings as S
from services.security_data.types import DATA_TYPE_LABELS, Point, ReadContext, Result

_PROVIDERS = None


def code_registry(override: dict | None = None) -> dict:
    global _PROVIDERS
    if override is not None:
        return override
    if _PROVIDERS is None:
        from services.security_data.providers import build_providers
        _PROVIDERS = build_providers()
    return _PROVIDERS


async def get(conn, org_id, data_type: str, subject: str, *, as_of: date, purpose: str = "internal",
              account_ids: list[str] | None = None, max_age_days: int | None = None,
              providers: dict | None = None) -> Result:
    out = await get_bulk(conn, org_id, data_type, [subject], as_of=as_of, purpose=purpose,
                         account_ids=account_ids, max_age_days=max_age_days, providers=providers)
    return out[str(subject)]


async def _plan(conn, org_id, data_type: str, ctx: ReadContext, providers: dict | None):
    """[(key, row, module | None, skip reason | None)] in rank order, plus the rank origin."""
    R.check_type(data_type)
    R.check_purpose(ctx.purpose)
    rows = await R.providers(conn)
    keys, origin = await R.rank_for(conn, org_id, data_type)
    code = code_registry(providers)
    plan = []
    for k in keys:
        p = rows.get(k)
        why = R.source_allowed(p, ctx)
        mod = code.get(k)
        if why is None and mod is None:
            why = "no provider module is installed"
        if why is None and data_type not in mod.capabilities:
            why = f"it does not supply {data_type}"
        if why is None and p.get("requires_entitlement") and not await R.entitled(conn, org_id, k):
            why = "the org has no entitlement to this provider"
        plan.append((k, p, mod, why))
    return plan, origin


async def get_bulk(conn, org_id, data_type: str, subjects: list[str], *, as_of: date, purpose: str = "internal",
                   account_ids: list[str] | None = None, max_age_days: int | None = None,
                   providers: dict | None = None) -> dict[str, Result]:
    subjects = [str(s) for s in dict.fromkeys(subjects) if s]
    ctx = ReadContext(str(org_id), purpose, account_ids, max_age_days)
    plan, origin = await _plan(conn, org_id, data_type, ctx, providers)
    limit = max_age_days if max_age_days is not None else await S.stale_days(conn, org_id, data_type)
    pct, _ = await S.setting(conn, org_id, "security_data.disagreement_pct")
    reads: dict[str, dict] = {}
    for k, p, mod, why in plan:
        if why is None:
            reads[k] = await mod.read(conn, ctx, data_type, subjects, end=as_of)
    out: dict[str, Result] = {}
    for s in subjects:
        tried, winner, wrow, alts = [], None, None, []
        for k, p, mod, why in plan:
            if why is not None:
                tried.append({"provider": k, "why": why})
                continue
            pts = reads[k].get(s) or []
            if not pts:
                tried.append({"provider": k, "why": "no value"})
                continue
            pt: Point = pts[0]
            lic_why = R.point_allowed(p, pt, ctx)
            if lic_why:
                tried.append({"provider": k, "why": lic_why})
                continue
            age = (as_of - pt.as_of).days
            if S.is_stale(age, limit):
                tried.append({"provider": k, "why": f"stale — latest is {pt.as_of.isoformat()}, {age} days old "
                                                    f"(limit {limit})", "as_of": pt.as_of.isoformat()})
                continue
            if winner is None:
                winner, wrow = pt, p
                tried.append({"provider": k, "why": "used"})
            else:
                alts.append(pt)
        if winner is None:
            label = DATA_TYPE_LABELS.get(data_type, data_type)
            reasons = "; ".join(f"{t['provider']}: {t['why']}" for t in tried) or "no source is ranked for it"
            out[s] = Result(data_type, s, False, missing=f"{data_type} ({label.lower()}) — {reasons}", tried=tried,
                            detail={"rank_origin": origin, "purpose": purpose})
            continue
        qual = list(winner.quality)
        dis = []
        if isinstance(winner.value, Decimal):
            for a in alts:
                if a.as_of == winner.as_of and isinstance(a.value, Decimal) and Q.disagreement(winner.value, a.value,
                                                                                                pct):
                    dis.append({"provider": a.provider_key, "value": format(a.value, "f")})
        if dis:
            qual.append("disagreement")
        lic = winner.licence or R.provider_licence(wrow)
        out[s] = Result(data_type, s, True, winner.value, winner.provider_key, wrow["name"], winner.as_of,
                        (as_of - winner.as_of).days, qual, lic, lic.get("attribution") or wrow.get("attribution"),
                        None, tried, {**winner.detail, "rank_origin": origin, "purpose": purpose,
                                      **({"disagreement": dis, "disagreement_pct": format(pct, "f")} if dis else {})})
    return out


async def history(conn, org_id, data_type: str, subject: str, *, start: date, end: date, purpose: str = "internal",
                  account_ids: list[str] | None = None, providers: dict | None = None) -> Result:
    """The first allowed provider (rank order) that holds ANY point in [start, end] — one source, never a
    splice of several. value = [(date, Decimal)] ascending."""
    ctx = ReadContext(str(org_id), purpose, account_ids)
    plan, origin = await _plan(conn, org_id, data_type, ctx, providers)
    tried = []
    for k, p, mod, why in plan:
        if why is not None:
            tried.append({"provider": k, "why": why})
            continue
        pts = (await mod.read(conn, ctx, data_type, [str(subject)], end=end, start=start)).get(str(subject)) or []
        pts = [x for x in pts if R.point_allowed(p, x, ctx) is None]
        if not pts:
            tried.append({"provider": k, "why": "no value in the range"})
            continue
        series = sorted((x.as_of, x.value) for x in pts)
        lic = pts[0].licence or R.provider_licence(p)
        return Result(data_type, str(subject), True, series, k, p["name"], series[-1][0], (end - series[-1][0]).days,
                      [], lic, lic.get("attribution"), None, tried + [{"provider": k, "why": "used"}],
                      {"rank_origin": origin, "points": len(series), "start": start.isoformat(), "end": end.isoformat()})
    label = DATA_TYPE_LABELS.get(data_type, data_type)
    reasons = "; ".join(f"{t['provider']}: {t['why']}" for t in tried) or "no source is ranked for it"
    return Result(data_type, str(subject), False, missing=f"{data_type} history ({label.lower()}) — {reasons}",
                  tried=tried, detail={"rank_origin": origin})
