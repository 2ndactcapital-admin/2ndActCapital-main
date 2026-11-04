/**
 * BUGFIX2 (BT-01): the readable reason for a failed API call, from FastAPI's
 * own `detail` — never a bare "Request failed" when the server said why.
 *
 *   string detail                → itself
 *   { message }                  → message
 *   [ {loc, msg}, … ] (422)      → "model_id: Input should be a valid UUID; …"
 *   nothing usable               → "Request failed (<status>)"
 */
export function errorFromDetail(detail, status) {
  if (typeof detail === "string" && detail.trim()) return detail;
  if (detail && typeof detail === "object" && !Array.isArray(detail)
      && typeof detail.message === "string" && detail.message.trim()) {
    return detail.message;
  }
  if (Array.isArray(detail) && detail.length) {
    const parts = detail
      .map((d) => {
        if (typeof d === "string") return d;
        if (!d || typeof d !== "object") return null;
        const loc = Array.isArray(d.loc) ? d.loc.filter((p) => p !== "body" && p !== "query" && p !== "path").join(".") : "";
        const msg = typeof d.msg === "string" ? d.msg : null;
        if (!msg) return null;
        return loc ? `${loc}: ${msg}` : msg;
      })
      .filter(Boolean);
    if (parts.length) return parts.join("; ");
  }
  return status ? `Request failed (${status})` : "Request failed";
}
