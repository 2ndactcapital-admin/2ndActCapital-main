/**
 * Interpreting a market API response for the page (mkt04a). Pure: no React, no
 * DOM, no fetch.
 *
 * FAIL CLOSED. The page renders its selection and grid controls only when
 * interpretCatalog returns { kind: "ready" }, and that requires a real
 * permissions envelope with can_read === true AND a vocabularies object. A
 * missing envelope, a malformed one, or one that does not grant read is an
 * error state — never a default that quietly shows the controls anyway.
 */

function isObject(v) {
  return v !== null && typeof v === "object" && !Array.isArray(v);
}

/**
 * The server's own message from an error body, unchanged. Handles the three
 * shapes that reach the page: {error} (the Next.js route's own 401/400/502),
 * {detail: "text"} (FastAPI HTTPException), and the market API's refusal
 * {detail: {message, errors: [{msg}]}}.
 */
export function errorMessage(body, fallback) {
  if (isObject(body)) {
    if (typeof body.error === "string" && body.error) return body.error;
    const d = body.detail;
    if (typeof d === "string" && d) return d;
    if (isObject(d)) {
      const msgs = Array.isArray(d.errors)
        ? d.errors.map((e) => (isObject(e) ? e.msg : null)).filter((m) => typeof m === "string" && m)
        : [];
      const head = typeof d.message === "string" ? d.message : "";
      if (head && msgs.length) return `${head}: ${msgs.join("; ")}`;
      if (head) return head;
      if (msgs.length) return msgs.join("; ");
    }
    if (Array.isArray(d)) {
      const msgs = d.map((e) => (isObject(e) ? e.msg : null)).filter((m) => typeof m === "string" && m);
      if (msgs.length) return msgs.join("; ");
    }
  }
  return fallback;
}

export const NO_ENVELOPE_MESSAGE =
  "Market data could not be shown: the response did not confirm your access.";

/** True only for an envelope that genuinely grants read. No fallback. */
export function grantsRead(body) {
  return isObject(body) && isObject(body.permissions) && body.permissions.can_read === true;
}

/**
 * { ok, body } of GET /api/market/catalog -> the page state.
 *   { kind: "error", message } | { kind: "ready", catalog }
 */
export function interpretCatalog({ ok, body }) {
  if (!ok) {
    return { kind: "error", message: errorMessage(body, "Market data could not be loaded.") };
  }
  if (!grantsRead(body) || !isObject(body.vocabularies)) {
    return { kind: "error", message: NO_ENVELOPE_MESSAGE };
  }
  const catalog = {
    categories: Array.isArray(body.categories) ? body.categories : [],
    indicators: Array.isArray(body.indicators) ? body.indicators : [],
    securities: Array.isArray(body.securities) ? body.securities : [],
    permissions: body.permissions,
    vocabularies: body.vocabularies,
  };
  return { kind: "ready", catalog };
}

/** A machine code as display text ("no_price_history" -> "no price history"). */
export function codeText(code) {
  return String(code ?? "").replace(/_/g, " ");
}

/**
 * Look a code up in a server vocabulary of [{key, label}] if the response
 * publishes one; otherwise the code text itself. Nothing is invented.
 */
export function vocabText(vocabList, code) {
  if (Array.isArray(vocabList)) {
    const hit = vocabList.find((v) => isObject(v) && v.key === code);
    if (hit && typeof hit.label === "string") return hit.label;
  }
  return codeText(code);
}
