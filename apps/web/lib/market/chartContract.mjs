/**
 * The ONLY place the chart names a server contract value (mkt04b).
 *
 * Each constant is a code the API defines, not a label: the two chart measures
 * (keys of vocabularies.modes), the transform-definition codes the browser must
 * emit exactly as the server does (docs/MARKET_DATA_DESIGN_V1.md, "Transform
 * definitions"), the saved-view config's two scale values ("Config schema"),
 * and the request frequencies whose period rule the time axis needs
 * ("Resampling"). No label lives here: every caption the chart shows for these
 * codes comes from the server's vocabularies (or the code text when the server
 * publishes none).
 *
 * verify_mkt04b.py's no-hardcoding scan exempts exactly this file and
 * lib/market/marketDefaults.mjs, and asserts this file holds only these
 * constants.
 */

// Chart measures: the two keys of vocabularies.modes the chart offers.
export const MEASURE_INDEX = "index";
export const MEASURE_SIGMA = "sigma";

// The default_transform whose series is "rate-like" when indexed.
export const TRANSFORM_LEVEL = "level";

// Warning and unavailable-reason codes, exactly as the server emits them.
export const WARNING_RATE_LIKE = "rate_like_series_indexed";
export const REASON_NON_POSITIVE_ANCHOR = "non_positive_anchor";
export const REASON_ZERO_VARIANCE = "zero_variance";
export const REASON_NO_OBSERVATIONS = "no_observations";

// Saved-view config scale values.
export const SCALE_LOG = "log";
export const SCALE_LINEAR = "linear";

// Request frequencies that bucket dates into periods (native and daily keep
// every observation date as its own period).
export const FREQ_WEEKLY = "weekly";
export const FREQ_MONTHLY = "monthly";
export const FREQ_QUARTERLY = "quarterly";
