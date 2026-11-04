/**
 * BUGFIX2 (AI-08): what a catalog list shows for its load state. The list used
 * to start as `rows = []` and render "0 models … No models on the platform
 * catalog yet." for as long as the (slow, LiteLLM-enriched) fetch took — and
 * kept saying it when the fetch failed. Now:
 *
 *   loading (never loaded)      → "Loading …", no count, no grid, no empty message
 *   error and never loaded      → the error, no count, no grid
 *   loaded                      → the real count and the grid (an error from a
 *                                 later reload is shown above the last good rows)
 *
 * `status` is "loading" | "ready" | "error"; `loaded` is true once any fetch
 * has succeeded.
 */
export function catalogView({ status, loaded, rows, error, noun = "model", nounPlural = null }) {
  const list = Array.isArray(rows) ? rows : [];
  const plural = nounPlural || `${noun}s`;
  if (!loaded) {
    if (status === "error") {
      return { showCount: false, countText: null, showGrid: false, loading: false,
        error: error || `Could not load the ${plural}.` };
    }
    return { showCount: false, countText: null, showGrid: false, loading: true, error: null };
  }
  return {
    showCount: true,
    countText: `${list.length} ${list.length === 1 ? noun : plural}`,
    showGrid: true,
    loading: status === "loading",
    error: status === "error" ? (error || `Could not reload the ${plural}.`) : null,
  };
}
