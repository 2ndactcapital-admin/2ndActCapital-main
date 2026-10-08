/**
 * The ONLY place the market page names a server vocabulary value (mkt04a).
 *
 * Each constant below is fixed by the mkt04a sprint or by the saved-view config
 * contract (docs/MARKET_DATA_DESIGN_V1.md, "Config schema"). Every other label,
 * option list, colour, limit and message on the page comes from the API
 * response. The defaults are validated against the server's own vocabulary at
 * runtime (lib/market/gridRequest.mjs initialControls): if the server stops
 * offering one, the page falls back to the server's first option instead of
 * sending a value the API would refuse.
 *
 * verify_mkt04a.py's no-hardcoding scan exempts exactly this file and asserts
 * it holds only these constants.
 */

// Grid control defaults (mkt04a Task 5a).
export const DEFAULT_MODE = "default";
export const DEFAULT_FREQUENCY = "monthly";
export const DEFAULT_ANCHOR_YEARS_BACK = 5;

// The one license class that needs no marker in the selection panel (Task 4).
export const UNRESTRICTED_LICENSE_CLASS = "public_domain";

// Saved-view selection kinds — the config contract mkt04c will persist.
export const KIND_INDICATOR = "indicator";
export const KIND_SECURITY = "security";
