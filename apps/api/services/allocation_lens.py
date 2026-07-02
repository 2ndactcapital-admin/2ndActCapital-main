"""Portfolio Allocation Lens — aggregation engine (Sprint 21).

Algorithm overview
------------------
1. resolve_entity_set → list of {entity_id, weight} pairs.
2. Fetch the latest entity_holdings snapshot per (entity_id, taxonomy_key)
   on or before *as_of*, then multiply each market_value by its entity weight
   to produce dollar-weighted actuals.
3. Fetch active member_target_allocations for those entities.
4. Dollar-weight-blend targets across entities:
   entity_dollar_weight = entity_total_$ / total_actual_$ (equal-weight when 0).
   blended_target[key] = sum(entity_dollar_weight * target_pct) over entities
   that carry a target for that key.
5. Mixed-level target reconciliation:
   – If a parent node has a stored blended target, use it.
   – Otherwise roll up by summing children targets.
   Sub targets are always the stored value (or 0).
6. Roll up ACTUALS sub → major → super by always summing children (plus any
   direct holdings stored at the parent level).
7. Classify each node's state vs target.
8. Return the full 3-level tree (every taxonomy node present, even at 0/0)
   plus aggregate metadata.
"""
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Optional
from uuid import UUID as _UUID

from services.entity_graph import resolve_entity_set
from services.taxonomy import build_taxonomy

UNDER_THRESHOLD = Decimal("0.75")
OVER_THRESHOLD = Decimal("1.15")
_ZERO = Decimal("0")
_HUNDRED = Decimal("100")
_PCT_PLACES = Decimal("0.0001")


def _classify_state(actual_pct: Decimal, target_pct: Decimal) -> str:
    """Return the allocation state label for a taxonomy node."""
    if target_pct == _ZERO and actual_pct == _ZERO:
        return "none"
    if target_pct == _ZERO and actual_pct > _ZERO:
        return "off_plan"
    if actual_pct < UNDER_THRESHOLD * target_pct:
        return "under"
    if actual_pct <= OVER_THRESHOLD * target_pct:
        return "on"
    return "over"


async def aggregate_allocation(
    pool,
    selector: dict,
    org_id: str,
    as_of: Optional[date] = None,
) -> dict[str, Any]:
    """Aggregate portfolio allocation data for a selector against taxonomy targets.

    Parameters
    ----------
    pool:
        asyncpg connection pool (statement_cache_size=0, per CLAUDE.md Rule 2).
    selector:
        Entity selector dict as accepted by resolve_entity_set.
    org_id:
        Organisation UUID string.
    as_of:
        Snapshot date ceiling for holdings lookup.  Defaults to today.

    Returns
    -------
    dict with keys: total_actual_dollar, currency, entity_count, as_of,
    super_classes (nested 3-level tree).
    """
    from datetime import date as _date

    if as_of is None:
        as_of = _date.today()

    # ------------------------------------------------------------------ #
    # 1. Resolve entity set                                                #
    # ------------------------------------------------------------------ #
    entity_weights = await resolve_entity_set(pool, org_id, selector)
    entity_ids = [ew["entity_id"] for ew in entity_weights]
    weight_by_entity: dict[str, Decimal] = {
        ew["entity_id"]: Decimal(ew["weight"]) for ew in entity_weights
    }
    entity_ids_pg = [_UUID(eid) for eid in entity_ids]
    entity_count = len(entity_ids)

    # ------------------------------------------------------------------ #
    # 2. Build taxonomy parent-relationship maps                           #
    # ------------------------------------------------------------------ #
    taxonomy = await build_taxonomy(org_id)

    sub_to_mc: dict[str, str] = {}
    sub_to_sc: dict[str, str] = {}
    mc_to_sc: dict[str, str] = {}
    sc_to_mcs: dict[str, list[str]] = {}
    mc_to_subs: dict[str, list[str]] = {}
    sc_info: dict[str, dict] = {}
    mc_info: dict[str, dict] = {}
    sub_info: dict[str, dict] = {}
    all_sc: list[str] = []
    all_mc: list[str] = []
    all_sub: list[str] = []

    for sc in taxonomy["super_classes"]:
        sc_key = sc["key"]
        all_sc.append(sc_key)
        sc_info[sc_key] = {"label": sc["label"], "order": sc.get("order", 0)}
        sc_to_mcs[sc_key] = []
        for mc in sc["major_classes"]:
            mc_key = mc["key"]
            all_mc.append(mc_key)
            mc_info[mc_key] = {"label": mc["label"], "sc_key": sc_key}
            mc_to_sc[mc_key] = sc_key
            sc_to_mcs[sc_key].append(mc_key)
            mc_to_subs[mc_key] = []
            for sub in mc["sub_categories"]:
                sub_key = sub["key"]
                all_sub.append(sub_key)
                sub_info[sub_key] = {"label": sub["label"], "mc_key": mc_key}
                sub_to_mc[sub_key] = mc_key
                sub_to_sc[sub_key] = sc_key
                mc_to_subs[mc_key].append(sub_key)

    # ------------------------------------------------------------------ #
    # 3. Fetch holdings and targets                                        #
    # ------------------------------------------------------------------ #
    holding_rows: list = []
    target_rows: list = []

    if entity_ids_pg:
        async with pool.acquire() as conn:
            # Latest snapshot per (entity_id, taxonomy_key) on or before as_of.
            holding_rows = await conn.fetch(
                """
                SELECT DISTINCT ON (entity_id, taxonomy_key)
                    entity_id, taxonomy_key, market_value
                FROM entity_holdings
                WHERE org_id = $1
                  AND entity_id = ANY($2::uuid[])
                  AND as_of_date <= $3
                ORDER BY entity_id, taxonomy_key, as_of_date DESC
                """,
                org_id,
                entity_ids_pg,
                as_of,
            )
            # Active targets: valid_to IS NULL AND system_to IS NULL.
            target_rows = await conn.fetch(
                """
                SELECT entity_id, taxonomy_key, taxonomy_level, target_pct
                FROM member_target_allocations
                WHERE org_id = $1
                  AND entity_id = ANY($2::uuid[])
                  AND valid_to IS NULL
                  AND system_to IS NULL
                """,
                org_id,
                entity_ids_pg,
            )

    # ------------------------------------------------------------------ #
    # 4. Compute dollar-weighted actuals per taxonomy key                  #
    # ------------------------------------------------------------------ #
    # leaf_dollars: holdings stored directly at each taxonomy key level,
    # weighted by the entity's selector weight.
    leaf_dollars: dict[str, Decimal] = {}
    entity_totals: dict[str, Decimal] = {eid: _ZERO for eid in entity_ids}

    for row in holding_rows:
        eid = str(row["entity_id"])
        key = row["taxonomy_key"]
        mv = Decimal(str(row["market_value"]))
        weight = weight_by_entity.get(eid, _ZERO)
        weighted_mv = mv * weight
        leaf_dollars[key] = leaf_dollars.get(key, _ZERO) + weighted_mv
        entity_totals[eid] = entity_totals.get(eid, _ZERO) + weighted_mv

    total_actual_dollar = sum(leaf_dollars.values(), _ZERO)

    # ------------------------------------------------------------------ #
    # 5. Entity dollar weights for blended target calculation              #
    # ------------------------------------------------------------------ #
    entity_dollar_weights: dict[str, Decimal] = {}
    if total_actual_dollar > _ZERO:
        for eid in entity_ids:
            entity_dollar_weights[eid] = entity_totals.get(eid, _ZERO) / total_actual_dollar
    else:
        # Equal-weight when no holdings exist.
        eq = Decimal("1") / Decimal(str(entity_count)) if entity_count > 0 else _ZERO
        for eid in entity_ids:
            entity_dollar_weights[eid] = eq

    # ------------------------------------------------------------------ #
    # 6. Blend stored targets across entities                              #
    # ------------------------------------------------------------------ #
    targets_by_entity: dict[tuple, Decimal] = {}
    for row in target_rows:
        eid = str(row["entity_id"])
        key = row["taxonomy_key"]
        pct = Decimal(str(row["target_pct"]))
        targets_by_entity[(eid, key)] = pct

    target_keys = {k for (_, k) in targets_by_entity}

    # blended_stored[key] = dollar-weight-blended target_pct across entities
    # that carry a stored target for this key.
    blended_stored: dict[str, Decimal] = {}
    for key in target_keys:
        total = _ZERO
        for eid in entity_ids:
            if (eid, key) in targets_by_entity:
                total += entity_dollar_weights[eid] * targets_by_entity[(eid, key)]
        blended_stored[key] = total

    # ------------------------------------------------------------------ #
    # 7. Mixed-level target reconciliation (stored → rollup from children) #
    # ------------------------------------------------------------------ #
    # Sub: always the stored blended value (or 0).
    sub_targets: dict[str, Decimal] = {
        sub_key: blended_stored.get(sub_key, _ZERO) for sub_key in all_sub
    }

    # Major: stored blended if present, else sum sub children.
    mc_targets: dict[str, Decimal] = {}
    for mc_key in all_mc:
        if mc_key in blended_stored:
            mc_targets[mc_key] = blended_stored[mc_key]
        else:
            mc_targets[mc_key] = sum(
                (sub_targets.get(s, _ZERO) for s in mc_to_subs.get(mc_key, [])),
                _ZERO,
            )

    # Super: stored blended if present, else sum major children.
    sc_targets: dict[str, Decimal] = {}
    for sc_key in all_sc:
        if sc_key in blended_stored:
            sc_targets[sc_key] = blended_stored[sc_key]
        else:
            sc_targets[sc_key] = sum(
                (mc_targets.get(m, _ZERO) for m in sc_to_mcs.get(sc_key, [])),
                _ZERO,
            )

    # ------------------------------------------------------------------ #
    # 8. Roll up ACTUALS sub → major → super                              #
    # ------------------------------------------------------------------ #
    # Sub: direct leaf dollars at sub key.
    sub_dollars: dict[str, Decimal] = {
        sub_key: leaf_dollars.get(sub_key, _ZERO) for sub_key in all_sub
    }

    # Major: direct leaf dollars at mc key + sum of sub children.
    mc_dollars: dict[str, Decimal] = {}
    for mc_key in all_mc:
        mc_dollars[mc_key] = leaf_dollars.get(mc_key, _ZERO) + sum(
            (sub_dollars.get(s, _ZERO) for s in mc_to_subs.get(mc_key, [])),
            _ZERO,
        )

    # Super: direct leaf dollars at sc key + sum of major children.
    sc_dollars: dict[str, Decimal] = {}
    for sc_key in all_sc:
        sc_dollars[sc_key] = leaf_dollars.get(sc_key, _ZERO) + sum(
            (mc_dollars.get(m, _ZERO) for m in sc_to_mcs.get(sc_key, [])),
            _ZERO,
        )

    # ------------------------------------------------------------------ #
    # 9. Percentage helper                                                 #
    # ------------------------------------------------------------------ #
    def to_pct(dollars: Decimal) -> Decimal:
        if total_actual_dollar > _ZERO:
            return (dollars / total_actual_dollar * _HUNDRED).quantize(
                _PCT_PLACES, rounding=ROUND_HALF_UP
            )
        return _ZERO

    # ------------------------------------------------------------------ #
    # 10. Build output tree                                                #
    # ------------------------------------------------------------------ #
    output_scs = []
    for sc_key in all_sc:
        sc_act_d = sc_dollars.get(sc_key, _ZERO)
        sc_act_pct = to_pct(sc_act_d)
        sc_tgt_pct = sc_targets.get(sc_key, _ZERO).quantize(_PCT_PLACES, rounding=ROUND_HALF_UP)
        sc_state = _classify_state(sc_act_pct, sc_tgt_pct)

        output_mcs = []
        for mc_key in sc_to_mcs.get(sc_key, []):
            mc_act_d = mc_dollars.get(mc_key, _ZERO)
            mc_act_pct = to_pct(mc_act_d)
            mc_tgt_pct = mc_targets.get(mc_key, _ZERO).quantize(_PCT_PLACES, rounding=ROUND_HALF_UP)
            mc_state = _classify_state(mc_act_pct, mc_tgt_pct)

            output_subs = []
            for sub_key in mc_to_subs.get(mc_key, []):
                sub_act_d = sub_dollars.get(sub_key, _ZERO)
                sub_act_pct = to_pct(sub_act_d)
                sub_tgt_pct = sub_targets.get(sub_key, _ZERO).quantize(
                    _PCT_PLACES, rounding=ROUND_HALF_UP
                )
                sub_state = _classify_state(sub_act_pct, sub_tgt_pct)

                output_subs.append(
                    {
                        "key": sub_key,
                        "label": sub_info[sub_key]["label"],
                        "level": "sub",
                        "actual_pct": float(sub_act_pct),
                        "target_pct": float(sub_tgt_pct),
                        "actual_dollar": float(sub_act_d),
                        "state": sub_state,
                    }
                )

            output_mcs.append(
                {
                    "key": mc_key,
                    "label": mc_info[mc_key]["label"],
                    "level": "major",
                    "actual_pct": float(mc_act_pct),
                    "target_pct": float(mc_tgt_pct),
                    "actual_dollar": float(mc_act_d),
                    "state": mc_state,
                    "sub_categories": output_subs,
                }
            )

        output_scs.append(
            {
                "key": sc_key,
                "label": sc_info[sc_key]["label"],
                "order": sc_info[sc_key]["order"],
                "level": "super",
                "actual_pct": float(sc_act_pct),
                "target_pct": float(sc_tgt_pct),
                "actual_dollar": float(sc_act_d),
                "state": sc_state,
                "major_classes": output_mcs,
            }
        )

    return {
        "total_actual_dollar": float(total_actual_dollar),
        "currency": "USD",
        "entity_count": entity_count,
        "as_of": as_of.isoformat(),
        "super_classes": output_scs,
    }
