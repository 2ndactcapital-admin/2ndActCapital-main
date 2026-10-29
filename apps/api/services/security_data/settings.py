"""Security-data settings: stated defaults, overridable per org through
org_settings (the same get_setting_with_origin path every rep sprint uses).
An unreadable or non-positive stored value falls back to the stated default."""

from __future__ import annotations

import uuid
from decimal import Decimal

# A value is STALE when today − as_of is MORE than the limit (exactly at the
# limit is still fresh). None = never stale on age (corporate actions are
# events, not a series).
STALE_DAYS_DEFAULT = {"eod_price": 5, "index_level": 5, "fx": 5, "option_quote": 1, "fund_holdings": 35,
                      "corporate_action": None}
SETTINGS: dict[str, Decimal] = {
    # two sources' same-day values differing by more than this fraction of the winner are flagged
    "security_data.disagreement_pct": Decimal("0.02"),
    # a daily move larger than this multiple of the series' median absolute daily move is an outlier
    "security_data.outlier_multiple": Decimal("10"),
    "security_data.outlier_min_moves": Decimal("20"),
    # a run of more than this many weekdays with no point between two points is a gap
    "security_data.gap_weekdays": Decimal("3"),
    # quality look-back window for the nightly (calendar days)
    "security_data.quality_window_days": Decimal("120"),
}
for _t, _v in STALE_DAYS_DEFAULT.items():
    if _v is not None:
        SETTINGS[f"security_data.stale_days.{_t}"] = Decimal(_v)


async def setting(conn, org_id, key: str) -> tuple[Decimal, str]:
    from services.org_settings import get_setting_with_origin

    default = SETTINGS[key]
    if org_id is None:
        return default, "default"
    try:
        v, is_default = await get_setting_with_origin(conn, uuid.UUID(str(org_id)), key)
    except Exception:  # noqa: BLE001 — an unreadable setting falls back to the stated default
        return default, "default"
    if v is None or is_default:
        return default, "default"
    try:
        dv = Decimal(str(v))
    except Exception:  # noqa: BLE001
        return default, "default"
    return (dv, "org_setting") if dv > 0 else (default, "default")


async def stale_days(conn, org_id, data_type: str) -> int | None:
    if STALE_DAYS_DEFAULT.get(data_type) is None:
        return None
    v, _ = await setting(conn, org_id, f"security_data.stale_days.{data_type}")
    return int(v)


def is_stale(age_days: int, limit: int | None) -> bool:
    """The ONE staleness rule: strictly more than the limit."""
    return limit is not None and age_days > limit
