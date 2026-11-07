"""ROLLUP2 Task 3 — a listed fund's allocation bucket comes from WHAT IT HOLDS, never from its kind.

Kind 'fund' covers equity, bond, muni, TIPS and crypto ETFs alike, and kind 'mutual_fund' defaulted to Public Equity
(taxonomy_sc_1) — "Fidelity U.S. Bond Index Fund" was keyed as equity. A fund's bucket is derived here from the
security master's CATEGORY data (``portfolio.security_reference_attributes`` field ``fund_category`` — the
FinanceDatabase load; 24 of the 25 Olivo fund assets carry one, docs/ROLLUP2_DISCOVERY.md 1e) through ONE map:

* ``DEFAULT_CATEGORY_MAP`` — (provider, category) → taxonomy key, keys only (Rule 4); ``None`` = deliberately no
  bucket (a multi-asset "Asset Allocation" fund stays Unclassified);
* ``portfolio.fund_category_taxonomy`` — Super Admin overrides (platform scope, valid-axis versions), merged over the
  default (docs/rollup2_part1.sql). Read / edit: ``GET`` / ``PUT /admin/fund-categories`` (Super Admin).

A key is used only if it is an ACTIVE asset_taxonomy key of the org (services.portfolio_kind_taxonomy).

The master's categories are coarse and sometimes wrong (FinanceDatabase files the Grayscale Bitcoin Mini Trust under
"Corporate Bonds" and VTI / SCHD under "Developed Markets"), so every proposal is cross-checked against the fund's
NAME: a name that clearly says another asset class (bitcoin / bond / treasury / money market / stock …) turns the
proposal into REVIEW — listed with both readings, never applied automatically. A fund with NO category stays
Unclassified (counted in net worth under "Unclassified") and is listed for the operator, with what its name suggests.

Statuses: ``apply`` (category → key, the name agrees or says nothing), ``unchanged`` (already that key), ``review``
(category and name disagree, or a legacy kind-default key the name contradicts), ``unclassified`` (no category).

SECMASTER2 — SHARPER CATEGORIES (docs/SECURITY_DATA_DESIGN.md, "Fund categories"):

* the CATEGORY GROUP decides an ambiguous category (FinanceDatabase files "Emerging Markets" under Equities AND
  Fixed Income, "Large Cap" under Derivatives too): the map is looked up as ``"<group> / <category>"`` first, then
  ``"<group> / *"``, then the bare category — so EM debt is Emerging Market Debt, a leveraged fund no bucket;
* the tree's US / international split (taxonomy_mc_1_1 US Large Cap, _1_2 US Small/Mid, _1_3 International
  Developed (+ sub_1_3_4 Global ex-US Blend, sub_1_3_5 International Small Cap), _1_4 Emerging Markets): where the
  category says only "equity" (taxonomy_sc_1 — "Developed Markets" holds VTI and VEA alike) or names a cap without
  a region, the fund's OWN DATA decides — its name's region tokens ("U.S.", "S&P 500", "Total Stock Market" → US;
  "International", "EAFE", "Developed Markets" → international developed; "ex US", "Total International" →
  global ex-US; "Emerging" → EM; "Total World", "Global" → no single region) and cap tokens (small / mid / large).
  FinanceDatabase states no country for an ETF or a mutual fund (only equities carry one), so the name is the
  data there is. Every proposal carries the reason ("category 'Developed Markets' → taxonomy_sc_1; name says US
  ('s&p 500') + large cap → taxonomy_mc_1_1");
* a contradiction is still REVIEW, never applied (bitcoin-named, filed Corporate Bonds);
* ``refine`` — a new status: the asset is already keyed with an ANCESTOR of the sharper key (taxonomy_sc_1 →
  taxonomy_mc_1_1). Listed as REFINE old → new with the reason; scripts/backfill_taxonomy_keys.py applies it only
  with ``--refine``. A different, non-ancestor key stays REVIEW.
"""

from __future__ import annotations

import re

PROVIDER = "finance_database"
FIELD = "fund_category"
TABLE = "portfolio.fund_category_taxonomy"
#: kinds whose bucket depends on holdings (money_market / municipal_bond have an honest kind-level key)
FUND_KINDS = frozenset({"fund", "etf", "mutual_fund", "closed_end_fund"})
#: the key bugfix3's kind map gave EVERY mutual fund (removed from portfolio.kind_taxonomy by ROLLUP2)
LEGACY_MUTUAL_FUND_DEFAULT = "taxonomy_sc_1"

DEFAULT_CATEGORY_MAP: dict[tuple[str, str], str | None] = {(PROVIDER, k.lower()): v for k, v in {
    # equity
    "Large Cap": "taxonomy_mc_1_1", "Mid Cap": "taxonomy_mc_1_2", "Small Cap": "taxonomy_mc_1_2",
    # FinanceDatabase files US total-market and dividend funds (VTI, SCHB, SCHD) under "Developed Markets" too, so
    # only the super class is honest here
    "Developed Markets": "taxonomy_sc_1",
    "Emerging Markets": "taxonomy_mc_1_4", "Frontier Markets": "taxonomy_mc_1_4", "China": "taxonomy_mc_1_4",
    "Communications": "taxonomy_sc_1", "Consumer Discretionary": "taxonomy_sc_1", "Consumer Staples": "taxonomy_sc_1",
    "Energy": "taxonomy_sc_1", "Financials": "taxonomy_sc_1", "Health Care": "taxonomy_sc_1",
    "Industrials": "taxonomy_sc_1", "Information Technology": "taxonomy_sc_1", "Materials": "taxonomy_sc_1",
    "Utilities": "taxonomy_sc_1", "Factors": "taxonomy_sc_1",
    # fixed income
    "Corporate Bonds": "taxonomy_mc_2_1", "Investment Grade Bonds": "taxonomy_mc_2_1",
    "High Yield Bonds": "taxonomy_mc_2_2", "Bank Loans": "taxonomy_sub_2_2_3",
    "Treasury Bonds": "taxonomy_sub_2_3_1", "Government Bonds": "taxonomy_mc_2_3",
    "Municipal Bonds": "taxonomy_sub_2_3_4", "Inflation-Protected Securities": "taxonomy_sub_2_3_2",
    "Mortgage-Backed Securities": "taxonomy_sub_2_3_5",
    # liquidity / real assets / alternatives
    "Money Market": "taxonomy_sub_8_1_1", "Currencies": "taxonomy_mc_8_2",
    "Real Estate": "taxonomy_mc_5_2", "Commodities": "taxonomy_mc_5_4",
    "Alternative": "taxonomy_sc_7",
    # multi-asset: no honest single bucket
    "Asset Allocation": None,
    # SECMASTER2 — FinanceDatabase's US mutual-fund categories (funds/NAS.csv) and the rest of the ETF list
    "Micro Cap": "taxonomy_mc_1_2", "Equities": "taxonomy_sc_1", "Technology": "taxonomy_sc_1",
    "Bonds": "taxonomy_sc_2", "Money Market Instruments": "taxonomy_sub_8_1_1", "REITs": "taxonomy_mc_5_2",
    "Commodities Broad Basket": "taxonomy_mc_5_4",
    "Value": None, "Growth": None, "Blend": None,        # a style, not a class: the group decides (below)
    "Target Date": None, "Allocation": None, "Trading": None, "Derivatives": None, "Unit Investment Trust": None,
    # group-qualified ("<group> / <category>", then "<group> / *"): the group decides an ambiguous category
    "Fixed Income / Emerging Markets": "taxonomy_mc_2_4", "Fixed Income / Frontier Markets": "taxonomy_mc_2_4",
    "Fixed Income / Bonds": "taxonomy_sc_2", "Fixed Income / Blend": "taxonomy_sc_2",
    "Fixed Income / Unit Investment Trust": "taxonomy_sc_2", "Fixed Income / Corporate Bonds": "taxonomy_mc_2_1",
    "Equities / Value": "taxonomy_sc_1", "Equities / Growth": "taxonomy_sc_1", "Equities / Blend": "taxonomy_sc_1",
    "Equities / Equities": "taxonomy_sc_1", "Equities / Unit Investment Trust": "taxonomy_sc_1",
    "Equities / Bonds": None,                             # the source disagrees with itself: no bucket
    "Cash / *": "taxonomy_sub_8_1_1",
    "Derivatives / *": None,                              # leveraged / inverse / options-overlay: no honest bucket
    "Target Date / *": None,
}.items()}
#: the default map's own keys are written "group / category" for the group-qualified entries
GROUP_SEP = " / "

#: name evidence → (super class, a more specific key or None). Order matters: the first match wins.
NAME_HINTS: tuple[tuple[str, str, str | None, str], ...] = (
    (r"\b(bitcoin|ethereum|ether|crypto|btc|solana)\b", "7", "taxonomy_mc_7_3", "digital asset"),
    (r"\bmoney market\b", "8", "taxonomy_sub_8_1_1", "money market"),
    (r"\b(bonds?|treasury|treasuries|municipal|tax[- ]exempt|tips|inflation[- ]protected|aggregate|fixed income|"
     r"t-bill)\b", "2", None, "fixed income"),
    (r"\b(equity|stocks?|s&p 500|500 index|total market|dividend|small[- ]cap|mid[- ]cap|large[- ]cap|russell|"
     r"nasdaq|msci)\b", "1", None, "equity"),
)


# ── SECMASTER2: region / cap from the fund's own name ─────────────────────────
#: (region, pattern) — the first match wins, most specific first: "All World ex US" is ex-US, not "world"
REGION_HINTS: tuple[tuple[str, str], ...] = (
    ("em", r"\b(emerging|frontier|china|chinese|india|latin america|brazil)\b"),
    ("ex_us", r"\b(ex[- ]?u\.?s\.?a?|ex[- ]us|total international|all[- ]world ex|world ex|global ex|acwi ex)\b"),
    ("intl", r"\b(international|intl|eafe|developed markets?|europe|european|japan|pacific|foreign|"
             r"ex[- ]north america)\b"),
    ("global", r"\b(total world|global|world|all[- ]country|acwi)\b"),
    ("us", r"(\bu\.s\.|\bus\b|\busa\b|\bamerica\b|s&p 500|\bs&p\b|total stock market|total market|"
           r"broad market|russell|nasdaq|\bdow\b|\bqqq\b|\b500\b|extended market|\bcrsp\b)"),
)
CAP_HINTS: tuple[tuple[str, str], ...] = (
    ("small", r"(small[- ]?cap|smallcap|russell 2000|s&p (small ?cap )?600|micro[- ]?cap)"),
    ("mid", r"(mid[- ]?cap|midcap|s&p (mid ?cap )?400)"),
    ("large", r"(large[- ]?cap|largecap|mega[- ]?cap|s&p 500|\b500\b|nasdaq[- ]100|\bqqq\b|dow jones industrial|"
              r"russell 1000)"),
)
EQUITY_REFINABLE = frozenset({"taxonomy_sc_1", "taxonomy_mc_1_1", "taxonomy_mc_1_2"})


def _first(hints, low: str):
    for k, rx in hints:
        m = re.search(rx, low)
        if m:
            return k, m.group(0)
    return None, None


def region_cap(name: str | None) -> dict:
    """What the fund's NAME says about region and cap: {"region", "region_token", "cap", "cap_token"}. Pure."""
    low = (name or "").lower()
    r, rt = _first(REGION_HINTS, low)
    c, ct = _first(CAP_HINTS, low)
    return {"region": r, "region_token": rt, "cap": c, "cap_token": ct}


def refine_equity(key: str, name: str | None, org_keys: set[str]) -> tuple[str, str | None]:
    """An equity key the category left coarse (super class, or a US cap key with no region) → the sharpest key the
    name supports, with the reason; (key, None) when the name adds nothing. A refined key not in the org's taxonomy
    falls back to the category's key."""
    if key not in EQUITY_REFINABLE:
        return key, None
    rc = region_cap(name)
    r, c = rc["region"], rc["cap"]
    rt = f"'{rc['region_token']}'" if rc["region_token"] else ""
    ct = f" + {c} cap ('{rc['cap_token']}')" if c else ""
    if r == "em":
        new, why = "taxonomy_mc_1_4", f"name says emerging markets ({rt})"
    elif r == "ex_us":
        new = "taxonomy_sub_1_3_5" if c == "small" else "taxonomy_sub_1_3_4"
        why = f"name says global ex-US ({rt}){ct}"
    elif r == "intl":
        new = "taxonomy_sub_1_3_5" if c == "small" else "taxonomy_mc_1_3"
        why = f"name says international developed ({rt}){ct}"
    elif r == "global":
        return key, f"name says global ({rt}) — US and international together: no single region bucket"
    elif r == "us" or (key in ("taxonomy_mc_1_1", "taxonomy_mc_1_2") and r is None):
        if c in ("small", "mid"):
            new = "taxonomy_mc_1_2"
        elif key == "taxonomy_mc_1_2" and c is None:
            new = key
        else:
            new = "taxonomy_mc_1_1"
        if r == "us":
            why = f"name says US ({rt}){ct}" + ("" if c else " — no cap stated: a US broad / total-market / "
                                                          "dividend fund is large-cap weighted (the tree has no US "
                                                          "all-cap key)")
        else:
            return key, None
    else:
        return key, "region not stated in the name — the super class only"
    if new not in org_keys:
        return key, f"{why} → {new}, not in this org's asset taxonomy — kept {key}"
    return new, f"{why} → {new}"


def is_ancestor(old: str | None, new: str | None) -> bool:
    """taxonomy_sc_1 is an ancestor of taxonomy_mc_1_3 and taxonomy_sub_1_3_4; taxonomy_mc_1_3 of sub_1_3_4."""
    mo = re.match(r"^taxonomy_(sc|mc|sub)_([0-9_]+)$", old or "")
    mn = re.match(r"^taxonomy_(sc|mc|sub)_([0-9_]+)$", new or "")
    if not mo or not mn or old == new:
        return False
    depth = {"sc": 1, "mc": 2, "sub": 3}
    po, pn = mo.group(2).split("_"), mn.group(2).split("_")
    return depth[mo.group(1)] < depth[mn.group(1)] and pn[:len(po)] == po


def lookup(mapping: dict, provider: str | None, category: str, group: str | None):
    """(found, key, how): "<group> / <category>", then "<group> / *", then the category."""
    prov = provider or PROVIDER
    cat = category.strip().lower()
    if group and group.strip():
        g = group.strip().lower()
        for k, how in (((prov, f"{g}{GROUP_SEP}{cat}"), f"group '{group}' / category '{category}'"),
                       ((prov, f"{g}{GROUP_SEP}*"), f"group '{group}'")):
            if k in mapping:
                return True, mapping[k], how
    if (prov, cat) in mapping:
        return True, mapping[(prov, cat)], f"category '{category}'"
    return False, None, f"category '{category}'"


def super_class(key: str | None) -> str | None:
    m = re.match(r"^taxonomy_(?:sc|mc|sub)_(\d+)", key or "")
    return m.group(1) if m else None


def name_hint(name: str | None) -> dict | None:
    """What the fund's NAME says: {"super", "key", "why"} or None. Pure."""
    low = (name or "").lower()
    for rx, sc, key, why in NAME_HINTS:
        m = re.search(rx, low)
        if m:
            return {"super": sc, "key": key or f"taxonomy_sc_{sc}", "why": f"name says {why} ('{m.group(0)}')"}
    return None


def propose_one(*, name: str | None, category: str | None, provider: str | None, current_key: str | None,
                asset_type: str | None, mapping: dict, org_keys: set[str], group: str | None = None) -> dict:
    """The proposal for ONE fund asset. Pure (the verify drives it directly). ``group`` (SECMASTER2) is the
    category group (FinanceDatabase category_group) when the master has one."""
    hint = name_hint(name)
    out = {"category": category, "category_group": group, "provider": provider, "current_key": current_key,
           "name_hint": hint, "key": None, "status": None, "reason": None, "region": region_cap(name)}
    legacy = asset_type == "mutual_fund" and current_key == LEGACY_MUTUAL_FUND_DEFAULT
    if not category:
        if legacy and hint and hint["super"] != "1":
            out.update(status="review", key=hint["key"] if hint["key"] in org_keys else None,
                       reason=f"keyed {current_key} by the old mutual-fund default (equity) but its {hint['why']}; "
                              "no category in the master — the operator decides")
        elif current_key:
            out.update(status="unchanged", key=current_key, reason="already keyed; no category in the master")
        else:
            out.update(status="unclassified", reason="no category in the security master — stays Unclassified"
                                                     + (f" ({hint['why']}; operator may --reclassify)" if hint else ""))
        return out
    found, key, how = lookup(mapping, provider, category, group)
    if not found:
        out.update(status="unclassified", reason=f"{how} has no entry in the fund category map")
        return out
    if key is None:
        out.update(status="unclassified", reason=f"{how} deliberately has no single bucket")
        return out
    if key not in org_keys:
        out.update(status="unclassified", reason=f"{how} → {key}, not in this org's asset taxonomy")
        return out
    if hint and hint["super"] != super_class(key):
        out.update(status="review", key=hint["key"] if hint["key"] in org_keys else None,
                   reason=f"{how} → {key} but the {hint['why']} — the master's category looks wrong")
        return out
    if hint and hint["key"] and hint["key"] != key and key == f"taxonomy_sc_{hint['super']}" \
            and hint["key"] in org_keys:
        key = hint["key"]                      # same class; the name gives the finer bucket (ETH → Digital Assets)
        reason = f"{how} → {key} (super class), refined by the {hint['why']}"
    else:
        reason = f"{how} → {key}"
    # SECMASTER2 — the US / international / EM and cap split, decided by the fund's own name
    refined, why = refine_equity(key, name, org_keys)
    if why:
        reason = f"{reason}; {why}"
    key = refined
    if current_key == key:
        out.update(status="unchanged", key=key, reason=reason + " (already)")
    elif current_key and not legacy and is_ancestor(current_key, key):
        out.update(status="refine", key=key, reason=f"{current_key} → {key}: {reason}")
    elif current_key and not legacy:
        out.update(status="review", key=key, reason=reason + f"; the asset already carries {current_key}")
    else:
        out.update(status="apply", key=key, reason=reason)
    return out


async def effective_map(conn) -> dict:
    """DEFAULT_CATEGORY_MAP merged with the live Super Admin overrides."""
    out = dict(DEFAULT_CATEGORY_MAP)
    for r in await conn.fetch(f"SELECT provider_key, category, taxonomy_key FROM {TABLE} "
                              "WHERE valid_to IS NULL AND system_to IS NULL"):
        out[(r["provider_key"], r["category"].strip().lower())] = r["taxonomy_key"]
    return out


async def categories(conn, security_ids: list) -> dict[str, tuple[str, str]]:
    """{global_security_id: (provider, category)} — the live platform category attribute."""
    ids = [str(s) for s in security_ids if s]
    if not ids:
        return {}
    rows = await conn.fetch(
        "SELECT DISTINCT ON (global_security_id) global_security_id::text AS g, provider_key, value "
        "FROM portfolio.security_reference_attributes WHERE global_security_id = ANY($1::uuid[]) AND field = $2 "
        "AND org_id IS NULL AND valid_to IS NULL AND system_to IS NULL "
        "ORDER BY global_security_id, (provider_key = $3) DESC, as_of DESC", ids, FIELD, PROVIDER)
    return {r["g"]: (r["provider_key"], r["value"]) for r in rows}


async def categories_full(conn, security_ids: list) -> dict[str, tuple[str, str, str | None]]:
    """SECMASTER2 — {global_security_id: (provider, category, category group | None)}: the category as
    ``categories`` reads it, plus the SAME provider's fund_category_group."""
    cats = await categories(conn, security_ids)
    if not cats:
        return {}
    groups = {(r["g"], r["provider_key"]): r["value"] for r in await conn.fetch(
        "SELECT global_security_id::text AS g, provider_key, value FROM portfolio.security_reference_attributes "
        "WHERE global_security_id = ANY($1::uuid[]) AND field = 'fund_category_group' AND org_id IS NULL "
        "AND valid_to IS NULL AND system_to IS NULL", list(cats))}
    return {g: (p, c, groups.get((g, p))) for g, (p, c) in cats.items()}


async def propose(conn, org_id) -> list[dict]:
    """Every current fund asset of the org (FUND_KINDS) with its proposal. Runs under the org's RLS context."""
    from services.portfolio_kind_taxonomy import org_taxonomy_keys

    org = str(org_id)
    assets = await conn.fetch(
        "SELECT a.id::text AS id, a.name, a.asset_type, a.default_taxonomy_key, a.global_security_id::text AS g, "
        "(SELECT count(*) FROM portfolio.positions p WHERE p.asset_id = a.id AND p.org_id = a.org_id "
        "AND p.valid_to IS NULL AND p.system_to IS NULL AND p.taxonomy_key IS NULL) AS live_unkeyed, "
        "(SELECT COALESCE(sum(p.market_value), 0) FROM portfolio.positions p WHERE p.asset_id = a.id "
        "AND p.org_id = a.org_id AND p.valid_to IS NULL AND p.system_to IS NULL) AS live_value "
        "FROM portfolio.assets a WHERE a.org_id = $1::uuid AND a.asset_type = ANY($2::text[]) "
        "AND a.valid_to IS NULL AND a.system_to IS NULL ORDER BY a.asset_type, a.name, a.id", org, sorted(FUND_KINDS))
    mapping = await effective_map(conn)
    keys = await org_taxonomy_keys(conn, org)
    cats = await categories_full(conn, [a["g"] for a in assets])
    names = {r["g"]: r["name"] for r in await conn.fetch(
        "SELECT id::text AS g, name FROM portfolio.securities_global WHERE id = ANY($1::uuid[]) AND valid_to IS NULL "
        "AND system_to IS NULL", [a["g"] for a in assets if a["g"]])} if any(a["g"] for a in assets) else {}
    out = []
    for a in assets:
        prov, cat, grp = cats.get(a["g"], (None, None, None))
        # the asset's own name first (what the adviser entered); the master's name adds region / cap tokens a
        # short asset name lacks (SECMASTER2: "Invesco QQQ Trust, Series 1" vs a placeholder never helps)
        nm = a["name"] if not names.get(a["g"]) or names[a["g"]] == a["name"] else f"{a['name']} | {names[a['g']]}"
        p = propose_one(name=nm, category=cat, provider=prov, current_key=a["default_taxonomy_key"],
                        asset_type=a["asset_type"], mapping=mapping, org_keys=keys, group=grp)
        out.append({"asset_id": a["id"], "name": a["name"], "asset_type": a["asset_type"],
                    "global_security_id": a["g"], "live_unkeyed_positions": int(a["live_unkeyed"]),
                    "live_value": a["live_value"], **p})
    return out


async def key_for_fund(conn, org_id, global_security_id) -> str | None:
    """The key a NEW fund asset gets at creation: only an ``apply`` proposal (category agrees with the name);
    anything else stays unkeyed → Unclassified (counted, listed)."""
    from services.portfolio_kind_taxonomy import org_taxonomy_keys

    if not global_security_id:
        return None
    g = await conn.fetchrow("SELECT name FROM portfolio.securities_global WHERE id = $1::uuid AND system_to IS NULL "
                            "ORDER BY valid_to NULLS FIRST LIMIT 1", str(global_security_id))
    prov, cat, grp = (await categories_full(conn, [global_security_id])).get(str(global_security_id),
                                                                             (None, None, None))
    p = propose_one(name=g["name"] if g else None, category=cat, provider=prov, current_key=None, asset_type="fund",
                    mapping=await effective_map(conn), org_keys=await org_taxonomy_keys(conn, str(org_id)), group=grp)
    return p["key"] if p["status"] == "apply" else None


async def list_map(conn) -> dict:
    """GET /admin/fund-categories — the effective map, the overrides, and the categories the master holds."""
    over = [dict(r) for r in await conn.fetch(
        f"SELECT id::text, provider_key, category, taxonomy_key, note, valid_from FROM {TABLE} "
        "WHERE valid_to IS NULL AND system_to IS NULL ORDER BY provider_key, lower(category)")]
    seen = {(r["provider_key"], r["value"].strip().lower()): int(r["n"]) for r in await conn.fetch(
        "SELECT provider_key, value, count(DISTINCT global_security_id) AS n FROM "
        "portfolio.security_reference_attributes WHERE field = $1 AND org_id IS NULL AND valid_to IS NULL "
        "AND system_to IS NULL GROUP BY 1, 2", FIELD)}
    eff = await effective_map(conn)
    rows = [{"provider_key": p, "category": c, "taxonomy_key": k, "securities": seen.get((p, c), 0),
             "source": "override" if any(o["provider_key"] == p and o["category"].strip().lower() == c for o in over)
             else "default"} for (p, c), k in sorted(eff.items())]
    unmapped = [{"provider_key": p, "category": c, "securities": n} for (p, c), n in sorted(seen.items())
                if (p, c) not in eff]
    return {"rows": rows, "overrides": over, "unmapped_categories": unmapped}


class FundMapError(ValueError):
    """Refused for a reason the caller can fix (422)."""


async def set_mapping(conn, *, actor_id, provider_key: str, category: str, taxonomy_key: str | None,
                      note: str | None) -> dict:
    """Record a Super Admin override (valid axis: close the live row, insert the new one). The connection must
    carry ``app.is_super_admin`` (the route uses platform_scope)."""
    prov, cat = (provider_key or "").strip(), (category or "").strip()
    if not prov or not cat:
        raise FundMapError("provider_key and category are required")
    if taxonomy_key is not None and not re.match(r"^taxonomy_(sc|mc|sub)_[0-9_]+$", taxonomy_key):
        raise FundMapError(f"{taxonomy_key!r} is not a taxonomy key (taxonomy_sc_N / _mc_ / _sub_)")
    async with conn.transaction():
        prior = await conn.fetchrow(
            f"UPDATE {TABLE} SET valid_to = now() WHERE provider_key = $1 AND lower(category) = lower($2) "
            "AND valid_to IS NULL AND system_to IS NULL RETURNING id::text, taxonomy_key", prov, cat)
        row = await conn.fetchrow(
            f"INSERT INTO {TABLE} (provider_key, category, taxonomy_key, note, created_by) VALUES ($1, $2, $3, $4, "
            "$5::uuid) RETURNING id::text, provider_key, category, taxonomy_key, note", prov, cat, taxonomy_key,
            (note or "").strip() or None, str(actor_id) if actor_id else None)
    return {**dict(row), "replaced": dict(prior) if prior else None}
