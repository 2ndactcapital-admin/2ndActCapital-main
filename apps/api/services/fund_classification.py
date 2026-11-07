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
}.items()}

#: name evidence → (super class, a more specific key or None). Order matters: the first match wins.
NAME_HINTS: tuple[tuple[str, str, str | None, str], ...] = (
    (r"\b(bitcoin|ethereum|ether|crypto|btc|solana)\b", "7", "taxonomy_mc_7_3", "digital asset"),
    (r"\bmoney market\b", "8", "taxonomy_sub_8_1_1", "money market"),
    (r"\b(bonds?|treasury|treasuries|municipal|tax[- ]exempt|tips|inflation[- ]protected|aggregate|fixed income|"
     r"t-bill)\b", "2", None, "fixed income"),
    (r"\b(equity|stocks?|s&p 500|500 index|total market|dividend|small[- ]cap|mid[- ]cap|large[- ]cap|russell|"
     r"nasdaq|msci)\b", "1", None, "equity"),
)


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
                asset_type: str | None, mapping: dict, org_keys: set[str]) -> dict:
    """The proposal for ONE fund asset. Pure (the verify drives it directly)."""
    hint = name_hint(name)
    out = {"category": category, "provider": provider, "current_key": current_key, "name_hint": hint,
           "key": None, "status": None, "reason": None}
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
    k = (provider or PROVIDER, category.strip().lower())
    if k not in mapping:
        out.update(status="unclassified", reason=f"category '{category}' has no entry in the fund category map")
        return out
    key = mapping[k]
    if key is None:
        out.update(status="unclassified", reason=f"category '{category}' deliberately has no single bucket")
        return out
    if key not in org_keys:
        out.update(status="unclassified", reason=f"category '{category}' → {key}, not in this org's asset taxonomy")
        return out
    if hint and hint["super"] != super_class(key):
        out.update(status="review", key=hint["key"] if hint["key"] in org_keys else None,
                   reason=f"category '{category}' → {key} but the {hint['why']} — the master's category looks wrong")
        return out
    if hint and hint["key"] and hint["key"] != key and key == f"taxonomy_sc_{hint['super']}" \
            and hint["key"] in org_keys:
        key = hint["key"]                      # same class; the name gives the finer bucket (ETH → Digital Assets)
        reason = f"category '{category}' → {key} (super class), refined by the {hint['why']}"
    else:
        reason = f"category '{category}' → {key}"
    if current_key == key:
        out.update(status="unchanged", key=key, reason=reason + " (already)")
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
    cats = await categories(conn, [a["g"] for a in assets])
    out = []
    for a in assets:
        prov, cat = cats.get(a["g"], (None, None))
        p = propose_one(name=a["name"], category=cat, provider=prov, current_key=a["default_taxonomy_key"],
                        asset_type=a["asset_type"], mapping=mapping, org_keys=keys)
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
    prov, cat = (await categories(conn, [global_security_id])).get(str(global_security_id), (None, None))
    p = propose_one(name=g["name"] if g else None, category=cat, provider=prov, current_key=None, asset_type="fund",
                    mapping=await effective_map(conn), org_keys=await org_taxonomy_keys(conn, str(org_id)))
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
