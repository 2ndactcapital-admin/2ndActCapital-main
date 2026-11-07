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


async def key_for_kind(c, org_id: str, asset_type: str | None, *, global_security_id=None) -> str | None:
    """The org's taxonomy key for ``asset_type``, or None (no mapping, or the mapped key is not in the org's
    taxonomy). ROLLUP2 — a FUND kind (fund / etf / mutual_fund / closed_end_fund) is never keyed by its kind: its
    bucket comes from the security master's category (services.fund_classification.key_for_fund) when the security
    is known and the category agrees with the name; otherwise None (counted as Unclassified)."""
    kind = (asset_type or "").strip()
    if not kind:
        return None
    from services.fund_classification import FUND_KINDS, key_for_fund

    if kind in FUND_KINDS:
        return await key_for_fund(c, org_id, global_security_id) if global_security_id else None
    key = (await kind_map(c, org_id)).get(kind)
    if not key:
        return None
    return key if key in await org_taxonomy_keys(c, org_id) else None


class ReclassifyError(ValueError):
    """The requested kind / key is not usable in this org's taxonomy (the caller can fix it)."""


async def reclassify_target(c, org_id: str, *, current_kind: str, target: str) -> tuple[str, str]:
    """CATCHALL1 — THE one rule for re-keying an asset's taxonomy when its kind is changed (or a key is named).

    ``target`` is a KIND (→ the key that kind maps to in ``portfolio.kind_taxonomy``) or, starting ``taxonomy_``, a
    KEY (the asset keeps ``current_kind``). Returns ``(kind, key)``; raises ReclassifyError when the kind has no key
    in the org's own taxonomy or the key is not an active one. Used by scripts/backfill_taxonomy_keys.py
    --reclassify AND by the direct-asset Edit (services.direct_holdings.edit_direct_asset) — never a copy."""
    keys = await org_taxonomy_keys(c, org_id)
    if target.startswith("taxonomy_"):
        if target not in keys:
            raise ReclassifyError(f"key {target!r} is not an active key of this org's taxonomy")
        return current_kind, target
    key = (await kind_map(c, org_id)).get(target)
    if not key or key not in keys:
        raise ReclassifyError(f"kind {target!r} has no key in this org's taxonomy")
    return target, key
