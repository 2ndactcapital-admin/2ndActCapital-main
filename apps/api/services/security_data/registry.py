"""Providers, ranks, entitlements and THE licence rule.

A provider is a registry ROW (portfolio.data_providers: kind, status, licence
facts, budget) plus a CODE module (services.security_data.providers.<key>).
A rank is an ordered provider list per data type: the org's override when it
has one for that type, else the platform default. An org override may name
only providers the platform rank lists for that type ("within the allowed
providers")."""

from __future__ import annotations

from services.security_data.types import DATA_TYPES, PURPOSES, Point, ReadContext, SecurityDataError

T_PROVIDERS = "portfolio.data_providers"
T_RANK = "portfolio.data_source_rank"
T_ENT = "portfolio.data_provider_entitlements"
LIVE = "valid_to IS NULL AND system_to IS NULL"


def check_type(data_type: str) -> None:
    if data_type not in DATA_TYPES:
        raise SecurityDataError(f"unknown data type {data_type!r} — one of {', '.join(DATA_TYPES)}")


def check_purpose(purpose: str) -> None:
    if purpose not in PURPOSES:
        raise SecurityDataError(f"unknown purpose {purpose!r} — one of {', '.join(PURPOSES)}")


async def providers(conn) -> dict[str, dict]:
    rows = await conn.fetch(f"SELECT * FROM {T_PROVIDERS} WHERE {LIVE} ORDER BY key")
    return {r["key"]: dict(r) for r in rows}


def provider_licence(p: dict) -> dict:
    return {"internal_use": bool(p["lic_internal_use"]), "member_display": p["lic_member_display"],
            "redistribution": bool(p["lic_redistribution"]), "retention_after_cancel": p["retention_after_cancel"],
            "attribution": p.get("attribution")}


async def platform_rank(conn, data_type: str) -> list[str]:
    rows = await conn.fetch(f"SELECT provider_key FROM {T_RANK} WHERE owner_scope = 'platform' AND data_type = $1 "
                            f"AND {LIVE} ORDER BY rank", data_type)
    return [r["provider_key"] for r in rows]


async def org_rank(conn, org_id, data_type: str) -> list[str]:
    rows = await conn.fetch(f"SELECT provider_key FROM {T_RANK} WHERE owner_scope = 'org' AND org_id = $1::uuid "
                            f"AND data_type = $2 AND {LIVE} ORDER BY rank", str(org_id), data_type)
    return [r["provider_key"] for r in rows]


async def rank_for(conn, org_id, data_type: str) -> tuple[list[str], str]:
    """(ordered provider keys, 'org_override' | 'platform')."""
    check_type(data_type)
    if org_id is not None:
        own = await org_rank(conn, org_id, data_type)
        if own:
            return own, "org_override"
    return await platform_rank(conn, data_type), "platform"


async def entitled(conn, org_id, provider_key: str) -> bool:
    if org_id is None:
        return False
    return bool(await conn.fetchval(f"SELECT 1 FROM {T_ENT} WHERE org_id = $1::uuid AND provider_key = $2 AND {LIVE}",
                                    str(org_id), provider_key))


def source_allowed(p: dict, ctx: ReadContext) -> str | None:
    """Provider-level gate: None when the provider may be asked at all, else why not."""
    if p is None:
        return "not registered"
    if p["status"] != "active":
        return "not connected" if p["status"] == "not_connected" else "retired"
    if ctx.purpose == "internal" and not p["lic_internal_use"]:
        return "its licence does not allow internal use"
    if ctx.purpose == "member_display" and p["lic_member_display"] == "not_allowed" and p["kind"] != "csv_upload":
        return "its licence does not allow showing values to members"
    return None


def point_allowed(p: dict, point: Point, ctx: ReadContext) -> str | None:
    """Value-level gate — THE licence rule. A value carrying its own licence (a
    file upload's, entered by the uploader) is judged by it; otherwise by the
    provider's. member_display 'own_holdings_only' passes only for a value the
    provider marked as the viewing member's own holding."""
    lic = point.licence or provider_licence(p)
    if ctx.purpose == "internal":
        return None if lic.get("internal_use") else "its licence does not allow internal use"
    md = lic.get("member_display")
    if md == "allowed" and lic.get("internal_use", True):
        return None
    if md == "own_holdings_only":
        return None if point.detail.get("own_holdings") else "shown to a member only for that member's own holdings"
    return "its licence does not allow showing values to members"
