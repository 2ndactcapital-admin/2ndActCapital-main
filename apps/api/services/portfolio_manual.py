"""PORTENTRY1 (GAP-PORT-01) — ORG-scoped manual assets and their relink to the platform master.

A held-away line for a holding the platform master does not carry (a mutual fund before the master has it, a muni
without a CUSIP source, a private holding, an option with no rep8 contract, a digital asset) becomes a row of
``portfolio.assets`` in the caller's org with ``global_security_id`` NULL — never a row of
``portfolio.securities_global``. Its identifiers go to ``portfolio.asset_identifiers`` (org-scoped), its terms
(coupon, maturity, option terms, chain) to ``assets.manual_terms``, its taxonomy key to ``default_taxonomy_key``.

RELINK — reported, never silent. :func:`relink_manual_assets` looks every org manual asset's CUSIP / ISIN / SEDOL /
ticker up in the master (read only). When one resolves to exactly one security the asset is linked to it on the
SYSTEM axis (Rule 3 — positions and valuations point at the asset's id, so the id is kept: an archived copy of the
prior version is written with ``system_to = now()`` and the live row gains ``global_security_id``), and a row is
written to ``portfolio.asset_relinks`` (the report the held-away panel and ``GET /portfolio/manual-assets`` show) plus
an audit row. A relinked asset is found by the held-away path for that security from then on (one asset, its
history intact).
"""

from __future__ import annotations

import json
import uuid
from datetime import date

from services.portfolio_assets import TABLE_ASSET_IDENT, TABLE_ASSETS, _OrgWrite, normalize_identifier_value

T_RELINK = "portfolio.asset_relinks"
LOOKUP_TYPES = ("cusip", "isin", "sedol", "ticker")
#: asset_kind → (asset_class, valuation_method). asset_type stores the kind itself (open text, no CHECK).
KIND_SHAPE = {
    "mutual_fund": ("financial", "nav"), "money_market": ("financial", "nav"),
    "municipal_bond": ("financial", "market_price"), "private": ("financial", "mark_to_model"),
    "listed_option": ("financial", "market_price"), "digital_asset": ("financial", "market_price"),
}


def _as_date(v) -> date | None:
    if v is None or v == "":
        return None
    if isinstance(v, date):
        return v
    return date.fromisoformat(str(v)[:10])


async def manual_asset_for(conn, org_id, ref: dict) -> str:
    """The org manual asset for ``ref`` (find by identifier, else by kind + name + terms; else create). Returns id."""
    org = str(org_id)
    kind = ref.get("asset_kind") or "private"
    idents = dict(ref.get("identifiers") or {})
    if ref.get("internal_id"):
        idents["internal"] = ref["internal_id"]
    terms = {k: v for k, v in (ref.get("terms") or {}).items() if k not in ("internal_id", "currency")}
    async with _OrgWrite(conn, org) as c:
        for t, v in idents.items():
            got = await c.fetchval(
                f"SELECT ai.asset_id::text FROM {TABLE_ASSET_IDENT} ai JOIN {TABLE_ASSETS} a ON a.id = ai.asset_id "
                f"WHERE ai.org_id = $1::uuid AND ai.id_type = $2 AND ai.id_value = $3 AND ai.valid_to IS NULL "
                f"AND ai.system_to IS NULL AND a.valid_to IS NULL AND a.system_to IS NULL LIMIT 1",
                org, t, normalize_identifier_value(t, v))
            if got:
                return got
        name = (ref.get("name") or "").strip() or kind
        got = await c.fetchval(
            f"SELECT id::text FROM {TABLE_ASSETS} WHERE org_id = $1::uuid AND global_security_id IS NULL "
            f"AND asset_type = $2 AND lower(btrim(name)) = lower($3) AND COALESCE(manual_terms, '{{}}'::jsonb) = $4::jsonb "
            f"AND valid_to IS NULL AND system_to IS NULL ORDER BY system_from LIMIT 1",
            org, kind, name, json.dumps(terms))
        if got:
            return got
        aclass, vmethod = KIND_SHAPE.get(kind, ("financial", "mark_to_model"))
        aid = str(uuid.uuid4())
        # PORTENTRY1 FIX 1 — the terms carry ISO strings (JSON); asyncpg binds a $n::date only from a date object.
        # A muni's maturity / an option's expiry as a str was the 500 on approve (DataError … 'toordinal').
        maturity = _as_date(terms.get("maturity") or terms.get("expiry"))
        await c.execute(
            f"""INSERT INTO {TABLE_ASSETS} (id, org_id, global_security_id, name, asset_class, asset_type,
                    ownership_basis, valuation_method, default_taxonomy_key, currency_code, maturity_date,
                    manual_terms)
                VALUES ($1::uuid, $2::uuid, NULL, $3, $4, $5, 'units', $6, $7, $8, $9::date, $10::jsonb)""",
            aid, org, name, aclass, kind, vmethod, ref.get("taxonomy_key"),
            ((ref.get("terms") or {}).get("currency") or "USD").upper(), maturity, json.dumps(terms))
        first = True
        for t, v in idents.items():
            await c.execute(
                f"INSERT INTO {TABLE_ASSET_IDENT} (asset_id, org_id, id_type, id_value, is_primary) "
                f"VALUES ($1::uuid, $2::uuid, $3, $4, $5)", aid, org, t, normalize_identifier_value(t, v), first)
            first = False
    return aid


async def relink_manual_assets(conn, org_id, *, trigger: str = "manual", actor_id=None) -> list[dict]:
    """Link every org manual asset whose identifier now resolves in the master (exactly one security). Returns the
    relinks it made — each also recorded in portfolio.asset_relinks and the audit log."""
    from services import securities_global as SG

    org = str(org_id)
    async with _OrgWrite(conn, org) as c:
        rows = await c.fetch(
            f"SELECT a.id::text AS asset_id, a.name, ai.id_type, ai.id_value FROM {TABLE_ASSETS} a "
            f"JOIN {TABLE_ASSET_IDENT} ai ON ai.asset_id = a.id AND ai.valid_to IS NULL AND ai.system_to IS NULL "
            f"WHERE a.org_id = $1::uuid AND a.global_security_id IS NULL AND a.asset_type <> 'cash' "
            f"AND ai.id_type = ANY($2::text[]) AND a.valid_to IS NULL AND a.system_to IS NULL "
            f"ORDER BY a.id, array_position($2::text[], ai.id_type)", org, list(LOOKUP_TYPES))
    if not rows:
        return []
    looks = {t: await SG.get_by_identifiers_bulk(conn, t, [r["id_value"] for r in rows if r["id_type"] == t])
             for t in LOOKUP_TYPES}
    done: dict[str, dict] = {}
    for r in rows:
        if r["asset_id"] in done:
            continue
        hit = looks[r["id_type"]].get(r["id_value"])
        if hit:
            done[r["asset_id"]] = {"asset_id": r["asset_id"], "asset_name": r["name"], "id_type": r["id_type"],
                                   "id_value": r["id_value"],
                                   "global_security_id": str(hit.get("matched_global_security_id") or hit["id"]),
                                   "security_name": hit.get("name")}
    out = []
    for x in done.values():
        async with _OrgWrite(conn, org) as c:
            live = await c.fetchrow(f"SELECT * FROM {TABLE_ASSETS} WHERE id = $1::uuid AND global_security_id IS NULL "
                                    "AND valid_to IS NULL AND system_to IS NULL FOR UPDATE", x["asset_id"])
            if live is None:
                continue
            cols = [k for k in live.keys() if k not in ("id",)]
            await c.execute(
                f"INSERT INTO {TABLE_ASSETS} ({', '.join(cols)}) SELECT "
                + ", ".join("now()" if k == "system_to" else k for k in cols)
                + f" FROM {TABLE_ASSETS} WHERE id = $1::uuid", x["asset_id"])
            await c.execute(f"UPDATE {TABLE_ASSETS} SET global_security_id = $2::uuid, system_from = now() "
                            "WHERE id = $1::uuid", x["asset_id"], x["global_security_id"])
            rid = await c.fetchval(
                f"""INSERT INTO {T_RELINK} (org_id, asset_id, global_security_id, id_type, id_value, asset_name,
                        security_name, relinked_by, trigger)
                    VALUES ($1::uuid, $2::uuid, $3::uuid, $4, $5, $6, $7, $8::uuid, $9) RETURNING id::text""",
                org, x["asset_id"], x["global_security_id"], x["id_type"], x["id_value"], x["asset_name"],
                x["security_name"], str(actor_id) if actor_id else None, trigger)
            await c.execute(
                "INSERT INTO audit_log (org_id, user_id, action, resource_type, resource_id, payload) "
                "VALUES ($1::uuid, $2::uuid, 'manual_asset_relinked', 'portfolio_asset', $3::uuid, $4::jsonb)",
                org, str(actor_id) if actor_id else None, x["asset_id"], json.dumps({**x, "relink_id": rid}))
        out.append({**x, "relink_id": rid, "trigger": trigger})
    return out


async def list_manual_assets(conn, org_id) -> dict:
    org = str(org_id)
    async with _OrgWrite(conn, org) as c:
        # BUGFIX3 (MUSE-OLIVO-M, Northline) — how many current positions (and distinct owners) use each manual
        # asset, so an org admin can SEE whether three held-away lines share one asset or made three.
        assets = await c.fetch(
            f"SELECT a.id::text AS id, a.name, a.asset_type, a.default_taxonomy_key, a.manual_terms, "
            f"a.global_security_id::text AS global_security_id, "
            f"(SELECT count(*) FROM portfolio.positions p WHERE p.asset_id = a.id AND p.org_id = a.org_id "
            f" AND p.valid_to IS NULL AND p.system_to IS NULL) AS position_count, "
            f"(SELECT count(DISTINCT p.owner_entity_id) FROM portfolio.positions p WHERE p.asset_id = a.id "
            f" AND p.org_id = a.org_id AND p.valid_to IS NULL AND p.system_to IS NULL) AS owner_count "
            f"FROM {TABLE_ASSETS} a WHERE a.org_id = $1::uuid "
            f"AND (a.global_security_id IS NULL OR EXISTS (SELECT 1 FROM {T_RELINK} r WHERE r.asset_id = a.id)) "
            f"AND a.asset_type <> 'cash' AND a.valid_to IS NULL AND a.system_to IS NULL ORDER BY a.name", org)
        relinks = await c.fetch(
            f"SELECT id::text, asset_id::text, global_security_id::text, id_type, id_value, asset_name, security_name, "
            f"trigger, created_at FROM {T_RELINK} WHERE org_id = $1::uuid AND valid_to IS NULL AND system_to IS NULL "
            f"ORDER BY created_at DESC", org)
    name_counts: dict[str, int] = {}
    for a in assets:
        k = (a["name"] or "").strip().lower()
        name_counts[k] = name_counts.get(k, 0) + 1
    return {"assets": [{**dict(a), "manual_terms": json.loads(a["manual_terms"]) if isinstance(a["manual_terms"], str)
                        else a["manual_terms"], "relinked": a["global_security_id"] is not None,
                        "position_count": int(a["position_count"]), "owner_count": int(a["owner_count"]),
                        # BUGFIX3 — another manual asset of this org carries the same name (a possible duplicate;
                        # reported, never merged automatically)
                        "same_name_count": name_counts[(a["name"] or "").strip().lower()]} for a in assets],
            "relinks": [{**dict(r), "created_at": r["created_at"].isoformat()} for r in relinks]}
