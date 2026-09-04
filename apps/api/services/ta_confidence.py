"""ta_confidence.py — confidence tier for a commitment's active TA parameters.

TA MODEL SPRINT 4, TASK 1a/4 — HONESTY NOTE.

The sprint prompt asserted that ``ConfidenceTier`` (OBSERVED / PEER_CALIBRATED
/ STRATEGY_DEFAULT / ASSUMED) and ``TAParameters.weakest_confidence`` were
"real, live fields on every projection's parameters" before this sprint. A
full grep of ``ta_model.py``, ``ta_config.py``, ``ta_calibrate.py`` and
``ta_params.py`` (Task 1a) found neither name anywhere in this codebase —
that premise was false, the same shape of false premise Sprint 1's own brief
documented for its own prior "93/93" claim. This module is the first real
implementation, not a wire-up of something that already existed.

Pure function, same no-DB discipline as ``ta_model.py``: the caller (the
router) already has ``portfolio.ta_model_params.source`` in scope from
``ta_params.get_active_params`` — this module never queries anything itself.

THREE of the prompt's four tiers ARE backed by real, existing data in this
codebase, derived from the one signal ``ta_model_params.source`` already
carries:
  - no active override row at all  -> STRATEGY_DEFAULT (an org's configured
    or platform-default parameters for the strategy, never fit to this
    commitment's own data)
  - source = 'override'            -> ASSUMED (a value an admin typed in
    directly, not derived from any realized cash-flow history)
  - source = 'calibrated'          -> OBSERVED (fit to this commitment's own
    real realized transactions by services.ta_calibrate.calibrate_strategy)

PEER_CALIBRATED — a tier implying calibration against SIMILAR funds' realized
behavior rather than this fund's own — has NO real data source anywhere in
this codebase: no cross-commitment/cross-fund aggregation of realized TA
history exists (grepped, confirmed absent). It is deliberately NOT
implemented here rather than silently faked from a value with no real
evidentiary basis. A future sprint that builds real peer aggregation can add
it; ``confidence_tier_for`` never returns it today.

There is also no per-parameter tier system (the prompt's "weakest_confidence"
implies several axes, one per parameter, with the lowest winning) — this
codebase persists exactly ONE active TAParams row per commitment, so there is
only one real tier to report, not several to take the min of. The router
publishes it as ``confidence_tier`` (singular), which IS the honest
equivalent of "weakest_confidence" when there is only one axis to begin with.
"""

from __future__ import annotations

STRATEGY_DEFAULT = "STRATEGY_DEFAULT"
ASSUMED = "ASSUMED"
OBSERVED = "OBSERVED"
#: Not implemented — see module docstring. Listed for completeness so a
#: caller checking "is this one of the four tiers the model names" can find
#: it, never returned by confidence_tier_for.
PEER_CALIBRATED = "PEER_CALIBRATED"

#: The tiers this codebase can actually compute today, in the module's own
#: honesty note above.
IMPLEMENTED_TIERS = (STRATEGY_DEFAULT, ASSUMED, OBSERVED)

_TIER_BY_SOURCE = {
    None: STRATEGY_DEFAULT,
    "override": ASSUMED,
    "calibrated": OBSERVED,
}

#: Plain-language explanation for each tier — Task 4 requires this be shown
#: as real prose, not just a color chip, so a member reading a STRATEGY_
#: DEFAULT-tier projection understands what it does and doesn't rest on.
TIER_DESCRIPTIONS = {
    STRATEGY_DEFAULT: (
        "This projection uses generic assumptions for this strategy — it has "
        "not been calibrated against this commitment's own realized cash flows."
    ),
    ASSUMED: (
        "This projection uses parameters entered directly by an administrator, "
        "not derived from any realized cash-flow history."
    ),
    OBSERVED: (
        "This projection is calibrated from this commitment's own realized "
        "capital calls and distributions."
    ),
}


def confidence_tier_for(source: str | None) -> str:
    """The real confidence tier for a ``ta_model_params.source`` value.

    ``source`` is ``None`` when a commitment has no active override row (the
    router falls back to strategy defaults in that case); otherwise it is
    whatever ``services.ta_params.set_override_params`` was called with —
    today always ``'override'`` or ``'calibrated'`` (see that module).
    """
    try:
        return _TIER_BY_SOURCE[source]
    except KeyError:
        raise ValueError(f"unrecognized ta_model_params.source: {source!r}") from None
