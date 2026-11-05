// BUGFIX3 (1e) — the "display lag": a list whose data is still loading (null) rendered its EMPTY state ("No direct
// assets.", "No addresses on file.") and only showed rows after a later re-read, so a screen looked empty, then
// full. One rule for every such list: null/undefined = loading (render "Loading…", never the empty sentence);
// an array (or an envelope's rows array) = loaded; a load error = error.

export function listView(data, { key = "rows", error = null } = {}) {
  if (error) return { state: "error", rows: [] };
  if (data === null || data === undefined) return { state: "loading", rows: [] };
  const rows = Array.isArray(data) ? data : data?.[key];
  if (!Array.isArray(rows)) return { state: "loading", rows: [] };
  return { state: rows.length ? "rows" : "empty", rows };
}
