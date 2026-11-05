"""BUGFIX3 FIX 1 — the taxonomy key an asset gets from its KIND when its creator supplied none.

The Olivo demo showed $0 net worth: all 23 live positions came from the manual creation paths (Add asset, Add private
investment, manual asset, integrated import), none of which set a taxonomy key, and ``portfolio_rollup`` skips a
position with no key (there is no bucket to put it in). This module is the ONE resolver every creation path calls:

* the kind → key map is the org setting ``portfolio.kind_taxonomy`` (org override merged over
  ``org_settings.DEFAULT_SETTINGS``) — keys only, never labels (Rule 1 / Rule 4);
* the key is accepted only if it is an ACTIVE ``asset_taxonomy`` row in the org's own ``config``; otherwise the asset
  stays unkeyed (and the rollup reports it as skipped) rather than being filed under a key the org does not have.

Runs on the caller's connection, which must already carry the org's RLS context (``_OrgWrite``) — config and
org_settings are both org-isolated, and a read without context returns nothing (it would silently resolve to None).
"""

from __future__ import annotations

import json

SETTING_KEY = "portfolio.kind_taxonomy"


def _decode(v):
    if isinstance(v, (str, bytes, bytearray)):
        try:
            return json.loads(v)
        except (TypeError, ValueError):
            return None
    return v


async def kind_map(c, org_id: str) -> dict[str, str]:
    """The effective kind → key map for the org (org override merged over the platform default)."""
    from services.org_settings import DEFAULT_SETTINGS

    out = dict(DEFAULT_SETTINGS.get(SETTING_KEY) or {})
    raw = await c.fetchval("SELECT setting_value FROM org_settings WHERE org_id = $1::uuid AND setting_key = $2",
                           str(org_id), SETTING_KEY)
    override = _decode(raw) if raw is not None else None
    if isinstance(override, dict):
        out.update({str(k): (str(v) if v else None) for k, v in override.items()})
    return {k: v for k, v in out.items() if v}


async def org_taxonomy_keys(c, org_id: str) -> set[str]:
    rows = await c.fetch("SELECT config_key FROM config WHERE org_id = $1::uuid AND category = 'asset_taxonomy' "
                         "AND (is_active IS NULL OR is_active = true)", str(org_id))
    return {r["config_key"] for r in rows}


async def key_for_kind(c, org_id: str, asset_type: str | None) -> str | None:
    """The org's taxonomy key for ``asset_type``, or None (no mapping, or the mapped key is not in the org's
    taxonomy)."""
    kind = (asset_type or "").strip()
    if not kind:
        return None
    key = (await kind_map(c, org_id)).get(kind)
    if not key:
        return None
    return key if key in await org_taxonomy_keys(c, org_id) else None
