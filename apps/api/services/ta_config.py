"""ta_config.py — per-strategy TA model defaults and org_settings resolution.

TA MODEL SPRINT 1. Resolves a :class:`services.ta_model.TAParams` for one of
the 8 seeded TA strategy keys, given a settings dict the CALLER already
fetched once from ``org_settings`` this request. This module never touches
the database itself — no ``import asyncpg``, no ``get_pool`` — the same
no-I/O discipline as ``ta_model.py``, so both remain trivially unit-testable
and so a router can prove (by object identity / call count) that settings
were fetched exactly once per request. See ``docs/TA_MODEL_INTEGRATION_BRIEF.md``
§Settings-caching for why this matters: this project's own
``/admin/platform`` theme surface previously shipped a bug where a settings
function was re-fetched mid-request and could observe a value that changed
between two reads of what should have been one consistent request.

TASK 1 FINDINGS (this sprint's discovery step — see the brief for the full
writeup):
  * No ``ta_strategy`` field, and no PE-strategy taxonomy of any kind, exists
    anywhere in the deployed schema or in ``config`` (asset_taxonomy is a
    generic SC/MC/Sub asset-class tree, not a PE-strategy vocabulary). The 8
    keys below are new.
  * No ``org_settings`` key under a ``modeling.*`` category was deployed
    before this sprint — this is genuinely greenfield, confirmed by direct
    query (Task 1c), not assumed.
  * There is no reusable ``seed_rows()`` helper anywhere in this codebase.
    The real, existing precedent for "default config that seeds an org's
    first read" is ``services.org_settings.DEFAULT_SETTINGS`` (a plain
    module dict, resolved lazily by ``get_setting``/``get_all_settings``) —
    that is the pattern this module follows below, and seeding the 4 keys
    for an org is a plain upsert through ``services.org_settings.set_setting``,
    not a new seeding subsystem.
"""

from __future__ import annotations

from decimal import Decimal

from services.ta_model import TAModelError, TAParams

TA_STRATEGY_KEYS = (
    "buyout",
    "growth_equity",
    "venture_capital",
    "real_estate",
    "real_assets",
    "private_credit",
    "fund_of_funds",
    "secondaries",
)

# org_settings keys — category 'modeling' (services.org_settings.category_for
# has no 'modeling.' prefix rule yet; CATEGORY_BY_PREFIX needs one entry added
# — see the router module, which adds it rather than duplicating the map
# here).
TA_STRATEGY_DEFAULTS_KEY = "modeling.ta.strategy_defaults"
TA_PROJECTION_HORIZON_YEARS_KEY = "modeling.ta.projection_horizon_years"
TA_DEFAULT_PERIODS_PER_YEAR_KEY = "modeling.ta.default_periods_per_year"
TA_CALIBRATION_MIN_YEARS_KEY = "modeling.ta.calibration_min_years"

TA_SETTINGS_KEYS = (
    TA_STRATEGY_DEFAULTS_KEY,
    TA_PROJECTION_HORIZON_YEARS_KEY,
    TA_DEFAULT_PERIODS_PER_YEAR_KEY,
    TA_CALIBRATION_MIN_YEARS_KEY,
)


def _p(rc: str, rd: str, g: str, bow: str, life: str) -> dict[str, str]:
    return {
        "rate_of_contribution": rc,
        "rate_of_distribution": rd,
        "growth_rate": g,
        "bow_factor": bow,
        "fund_life_years": life,
    }


# Starting defaults per strategy — industry-typical relative J-curve shapes,
# not fitted to any specific fund. growth/venture call and grow NAV faster but
# distribute later than buyout; private credit calls fast and distributes
# early (income-generating, short bow); real assets/real estate sit between
# buyout and credit; secondaries call and distribute fastest (already-seasoned
# NAV at entry, shorter remaining life); fund-of-funds has the mildest curve
# (a double J-curve smooths both ends). An org_admin overrides these via
# PUT /api/v1/admin/modeling/ta/defaults; a commitment-level override or
# calibration further overrides per-commitment (portfolio.ta_model_params).
#
# rate_of_contribution / rate_of_distribution / growth_rate are PER-PERIOD
# figures at DEFAULT_PERIODS_PER_YEAR (quarterly) — NOT the annual rates a
# reader would naively picture. They are the compound-equivalent quarterly
# rate for the annual target in parentheses, i.e. rc_q solves
# ``(1 - rc_q) ** 4 == 1 - rc_annual`` and g_q solves
# ``(1 + g_q) ** 4 == 1 + g_annual`` — an annual 11% NAV growth rate applied
# directly as a QUARTERLY rate compounds to ~52%/year, which is exactly the
# kind of frequency mismatch Task 3 (the calibration floor) warns about
# in the other direction. A strategy resolved at a different
# ``periods_per_year`` must recompute these, not merely relabel them.
DEFAULT_TA_STRATEGY_PARAMS: dict[str, dict[str, str]] = {
    # buyout          (annual: rc=0.28, rd=0.22, g=0.11)
    "buyout":          _p("0.0788", "0.0602", "0.02643", "2.2", "10"),
    # growth_equity    (annual: rc=0.30, rd=0.18, g=0.16)
    "growth_equity":   _p("0.0853", "0.0484", "0.03780", "2.4", "9"),
    # venture_capital  (annual: rc=0.25, rd=0.15, g=0.20)
    "venture_capital": _p("0.0694", "0.0398", "0.04664", "2.8", "11"),
    # real_estate      (annual: rc=0.30, rd=0.20, g=0.08)
    "real_estate":     _p("0.0853", "0.0543", "0.01943", "1.8", "8"),
    # real_assets      (annual: rc=0.28, rd=0.22, g=0.07)
    "real_assets":     _p("0.0788", "0.0602", "0.01706", "1.6", "9"),
    # private_credit   (annual: rc=0.45, rd=0.35, g=0.06)
    "private_credit":  _p("0.1388", "0.1021", "0.01467", "1.2", "6"),
    # fund_of_funds    (annual: rc=0.20, rd=0.15, g=0.10)
    "fund_of_funds":   _p("0.0543", "0.0398", "0.02411", "1.8", "12"),
    # secondaries      (annual: rc=0.40, rd=0.30, g=0.10)
    "secondaries":     _p("0.1199", "0.0853", "0.02411", "1.3", "7"),
}

DEFAULT_PROJECTION_HORIZON_YEARS = 10
DEFAULT_PERIODS_PER_YEAR = 4
DEFAULT_CALIBRATION_MIN_YEARS = 3


class TAConfigError(ValueError):
    """A strategy key or settings shape was invalid — maps to HTTP 400/422."""


def params_for_strategy(
    strategy_key: str,
    settings: dict,
    *,
    periods_per_year: int | None = None,
) -> TAParams:
    """Resolve one strategy's :class:`TAParams` from an already-fetched
    settings dict (``services.org_settings.get_all_settings`` or equivalent,
    called ONCE by the router this request).

    Falls back to :data:`DEFAULT_TA_STRATEGY_PARAMS` for any strategy key the
    org has not overridden, and to :data:`DEFAULT_PERIODS_PER_YEAR` /
    :data:`DEFAULT_PROJECTION_HORIZON_YEARS` for the frequency/horizon keys —
    mirroring ``org_settings.get_setting``'s own default-fallback behaviour,
    which this module deliberately does not re-implement by calling back into
    the database.
    """
    if strategy_key not in TA_STRATEGY_KEYS:
        raise TAConfigError(f"strategy_key={strategy_key!r} is not one of {TA_STRATEGY_KEYS}")

    strategy_defaults = settings.get(TA_STRATEGY_DEFAULTS_KEY) or DEFAULT_TA_STRATEGY_PARAMS
    raw = strategy_defaults.get(strategy_key) or DEFAULT_TA_STRATEGY_PARAMS[strategy_key]

    resolved_ppy = periods_per_year or int(
        settings.get(TA_DEFAULT_PERIODS_PER_YEAR_KEY) or DEFAULT_PERIODS_PER_YEAR
    )

    try:
        return TAParams(
            rate_of_contribution=Decimal(str(raw["rate_of_contribution"])),
            rate_of_distribution=Decimal(str(raw["rate_of_distribution"])),
            growth_rate=Decimal(str(raw["growth_rate"])),
            bow_factor=Decimal(str(raw["bow_factor"])),
            fund_life_years=Decimal(str(raw["fund_life_years"])),
            periods_per_year=resolved_ppy,
        )
    except TAModelError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise TAConfigError(f"malformed TA params for {strategy_key!r}: {raw!r}") from exc


def projection_horizon_periods(settings: dict, periods_per_year: int) -> int:
    """The default projection horizon, in PERIODS at ``periods_per_year``."""
    years = int(settings.get(TA_PROJECTION_HORIZON_YEARS_KEY) or DEFAULT_PROJECTION_HORIZON_YEARS)
    return years * periods_per_year


def default_settings_seed() -> dict[str, object]:
    """The 4 org_settings rows this sprint seeds for the default org (Task 2).

    Returns ``{setting_key: value}``. The caller upserts each key through
    ``services.org_settings.set_setting`` — the real, existing upsert path —
    once per key, under the same permission check every other org_settings
    write goes through (``can_manage_org_settings``). There is no separate
    seeding subsystem to build.
    """
    return {
        TA_STRATEGY_DEFAULTS_KEY: DEFAULT_TA_STRATEGY_PARAMS,
        TA_PROJECTION_HORIZON_YEARS_KEY: DEFAULT_PROJECTION_HORIZON_YEARS,
        TA_DEFAULT_PERIODS_PER_YEAR_KEY: DEFAULT_PERIODS_PER_YEAR,
        TA_CALIBRATION_MIN_YEARS_KEY: DEFAULT_CALIBRATION_MIN_YEARS,
    }
