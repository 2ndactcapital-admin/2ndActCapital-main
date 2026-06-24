"""Taxonomy service: build and cache the nested asset taxonomy from config rows.

The taxonomy is stored flat in the ``config`` table
(category='asset_taxonomy') with three value_type levels:
  super_class   → taxonomy_sc_{n}
  major_class   → taxonomy_mc_{sc}_{mc}
  sub_category  → taxonomy_sub_{sc}_{mc}_{sub}

Parent relationships are encoded in the config_key, not as FK columns.
This module parses the keys, assembles the nested tree in Python, and
exposes helpers used by the /taxonomy endpoint and deal validation.
"""

from __future__ import annotations

import re
from typing import Any

from services.database import get_pool


def _parse_key(key: str) -> tuple[str, list[int]] | None:
    """Return (level, [numeric_segments]) or None if not a taxonomy key."""
    if m := re.fullmatch(r"taxonomy_sc_(\d+)", key):
        return "super_class", [int(m.group(1))]
    if m := re.fullmatch(r"taxonomy_mc_(\d+)_(\d+)", key):
        return "major_class", [int(m.group(1)), int(m.group(2))]
    if m := re.fullmatch(r"taxonomy_sub_(\d+)_(\d+)_(\d+)", key):
        return "sub_category", [int(m.group(1)), int(m.group(2)), int(m.group(3))]
    return None


def _sc_key(sc: int) -> str:
    return f"taxonomy_sc_{sc}"


def _mc_key(sc: int, mc: int) -> str:
    return f"taxonomy_mc_{sc}_{mc}"


async def build_taxonomy(org_id: str) -> dict[str, Any]:
    """Fetch all active taxonomy config rows and assemble into a nested tree."""
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT config_key, config_value, display_order
        FROM config
        WHERE org_id = $1
          AND category = 'asset_taxonomy'
          AND (is_active IS NULL OR is_active = true)
        ORDER BY display_order NULLS LAST, config_key
        """,
        org_id,
    )

    # Index rows by key for O(1) lookup during assembly.
    by_key: dict[str, dict] = {}
    for r in rows:
        parsed = _parse_key(r["config_key"])
        if parsed is None:
            continue
        level, segs = parsed
        by_key[r["config_key"]] = {
            "key": r["config_key"],
            "label": r["config_value"],
            "order": r["display_order"] or 0,
            "level": level,
            "segs": segs,
        }

    # Build super-class shells.
    sc_map: dict[str, dict] = {}
    for entry in by_key.values():
        if entry["level"] != "super_class":
            continue
        sc_map[entry["key"]] = {
            "key": entry["key"],
            "label": entry["label"],
            "order": entry["order"],
            "major_classes": [],
        }

    # Attach major classes.
    mc_map: dict[str, dict] = {}
    for entry in by_key.values():
        if entry["level"] != "major_class":
            continue
        sc, mc = entry["segs"]
        parent_key = _sc_key(sc)
        if parent_key not in sc_map:
            continue
        node = {
            "key": entry["key"],
            "label": entry["label"],
            "order": entry["order"],
            "super_class_key": parent_key,
            "sub_categories": [],
        }
        sc_map[parent_key]["major_classes"].append(node)
        mc_map[entry["key"]] = node

    # Attach sub-categories.
    for entry in by_key.values():
        if entry["level"] != "sub_category":
            continue
        sc, mc, _ = entry["segs"]
        parent_key = _mc_key(sc, mc)
        if parent_key not in mc_map:
            continue
        mc_map[parent_key]["sub_categories"].append(
            {
                "key": entry["key"],
                "label": entry["label"],
                "order": entry["order"],
                "major_class_key": parent_key,
            }
        )

    # Sort each level by display_order.
    for sc in sc_map.values():
        sc["major_classes"].sort(key=lambda x: x["order"])
        for mc in sc["major_classes"]:
            mc["sub_categories"].sort(key=lambda x: x["order"])

    super_classes = sorted(sc_map.values(), key=lambda x: x["order"])
    return {"super_classes": super_classes}


async def get_taxonomy_index(org_id: str) -> dict[str, str]:
    """Return a flat dict of {config_key: label} for all taxonomy rows.

    Used by deal validation to resolve label → key lookups quickly.
    """
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT config_key, config_value, value_type
        FROM config
        WHERE org_id = $1
          AND category = 'asset_taxonomy'
          AND (is_active IS NULL OR is_active = true)
        """,
        org_id,
    )
    return {r["config_key"]: {"label": r["config_value"], "type": r["value_type"]} for r in rows}


async def validate_taxonomy_fields(
    org_id: str,
    asset_super_class: str | None,
    asset_class: str | None,
    asset_sub_category: str | None,
) -> list[str]:
    """Validate taxonomy key fields against the config table.

    Returns a list of error messages (empty = valid).
    All three fields are optional; only present values are validated.
    """
    if not any([asset_super_class, asset_class, asset_sub_category]):
        return []

    index = await get_taxonomy_index(org_id)
    errors: list[str] = []

    if asset_super_class:
        sc_entry = index.get(asset_super_class)
        if not sc_entry or sc_entry["type"] != "super_class":
            errors.append(
                f"asset_super_class '{asset_super_class}' is not a valid taxonomy key"
            )

    if asset_class:
        mc_entry = index.get(asset_class)
        if not mc_entry or mc_entry["type"] != "major_class":
            errors.append(
                f"asset_class '{asset_class}' is not a valid taxonomy key"
            )
        elif asset_super_class:
            # Check that the major class belongs to the given super class.
            parsed = _parse_key(asset_class)
            if parsed:
                _, segs = parsed
                if _sc_key(segs[0]) != asset_super_class:
                    sc_label = index.get(asset_super_class, {}).get("label", asset_super_class)
                    mc_label = mc_entry["label"]
                    errors.append(
                        f"asset_class '{mc_label}' not found under "
                        f"super_class '{sc_label}'"
                    )

    if asset_sub_category:
        sub_entry = index.get(asset_sub_category)
        if not sub_entry or sub_entry["type"] != "sub_category":
            errors.append(
                f"asset_sub_category '{asset_sub_category}' is not a valid taxonomy key"
            )
        elif asset_class:
            parsed = _parse_key(asset_sub_category)
            if parsed:
                _, segs = parsed
                if _mc_key(segs[0], segs[1]) != asset_class:
                    mc_label = index.get(asset_class, {}).get("label", asset_class)
                    sub_label = sub_entry["label"]
                    errors.append(
                        f"asset_sub_category '{sub_label}' not found under "
                        f"asset_class '{mc_label}'"
                    )

    return errors
