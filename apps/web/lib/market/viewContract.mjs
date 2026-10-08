/**
 * The ONLY place mkt04c names a server contract value.
 *
 * Each constant is a code fixed by the saved-view config contract or the
 * key-dates response (docs/MARKET_DATA_DESIGN_V1.md, "Config schema" and "Key
 * dates"), not a label: the config version, the two anchor shapes, and the
 * month precision of a key date. Every caption the page shows comes from the
 * server's responses.
 *
 * verify_mkt04c.py's no-hardcoding scan exempts this file and asserts it holds
 * only these constants.
 */

// Saved-view config version ("v").
export const CONFIG_VERSION = 1;

// Saved-view anchor / end shapes ("type").
export const ANCHOR_DATE = "date";
export const ANCHOR_RELATIVE = "relative";

// A key date stored as the first of its month (start_precision / end_precision).
export const PRECISION_MONTH = "month";
