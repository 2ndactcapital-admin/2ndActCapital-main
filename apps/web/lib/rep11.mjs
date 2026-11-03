// REP11 — structured notes: the pure logic behind Replication → Notes, a
// note's page, a replica's page and the X-ray's held-note marker. Every control
// is decided HERE from the server's envelope and FAILS CLOSED: a control shows
// only for `=== true` on the server's own flag AND an action the server listed.
// A missing, partial or malformed envelope shows none. Every label comes from
// the envelope's vocabularies. A replica cost / embedded fee is an ESTIMATE,
// shown only as the server sends it ("not priced — enter a quote …" or
// withheld under the hypothetical-performance policy) — never a placeholder.

const isTrue = (v) => v === true;
const listOf = (x) => (Array.isArray(x) ? x : []);

export function perms(envelope) {
  const p = envelope && typeof envelope === "object" ? envelope.permissions : null;
  return p && typeof p === "object" ? p : {};
}

export function vocab(envelope) {
  const v = envelope && typeof envelope === "object" ? envelope.vocabularies : null;
  return v && typeof v === "object" ? v : {};
}

export function labelOf(list, key) {
  if (!Array.isArray(list)) return key ?? "";
  const hit = list.find((x) => x && x.key === key);
  return hit ? hit.label : (key ?? "");
}

const has = (obj, a) => listOf(obj?.actions).includes(a);

/** Notes list: "recompute verdicts" needs can_write and the listed action. */
export function listControls(envelope) {
  return { runVerdicts: isTrue(perms(envelope).can_write) && has(envelope, "run_verdicts") };
}

/** A note's page: build a replica needs can_write, the listed action and a non-empty editable vocabulary. */
export function noteControls(envelope) {
  const w = isTrue(perms(envelope).can_write);
  return { build: w && has(envelope, "build") && listOf(vocab(envelope).editable).length > 0 };
}

/** A replica's page: price / submit / archive need can_write and the action; approve / reject need can_approve
 * and the action (never listed for a maker). */
export function replicaControls(envelope) {
  const p = perms(envelope);
  const w = isTrue(p.can_write);
  const ap = isTrue(p.can_approve);
  return { price: w && has(envelope, "price"), submit: w && has(envelope, "submit"),
    archive: w && has(envelope, "archive"), approve: ap && has(envelope, "approve"),
    reject: ap && has(envelope, "reject") };
}

/** A decimal string as a percent ("0.2" → "20.00%"); "—" for none. */
export function pct(v, digits = 2) {
  if (v === null || v === undefined || v === "") return "—";
  const n = Number(v);
  if (!Number.isFinite(n)) return "—";
  return `${(n * 100).toFixed(digits)}%`;
}

/** A per-100-of-principal figure ("98.75" → "98.7500"). */
export function per100(v) {
  if (v === null || v === undefined || v === "") return "—";
  const n = Number(v);
  return Number.isFinite(n) ? n.toFixed(4) : "—";
}

/** What the estimate line says: the server's figures, its "not priced" text, or the policy's withholding. */
export function estimateText(est) {
  if (!est || typeof est !== "object") return "not priced — enter a quote";
  if (est.withheld === true) return `Estimate withheld — ${est.text || "staff use only"}`;
  if (est.status === "priced") {
    return `Embedded fee ${per100(est.embedded_fee_pct)} per 100 (note ${per100(est.note_price_pct)} − replica ${per100(est.replica_cost_pct)}), as of ${est.as_of} — estimate`;
  }
  return est.text || "not priced — enter a quote";
}

/** The overlay's two series in SVG coordinates (x = underlying return, y = return), from the server's points. */
export function overlay(points, { width = 520, height = 260, pad = 30 } = {}) {
  const pts = listOf(points).map((p) => ({ x: Number(p.underlying_return), n: Number(p.note_return), r: Number(p.replica_return) }))
    .filter((p) => [p.x, p.n, p.r].every(Number.isFinite));
  if (!pts.length) return null;
  const xs = pts.map((p) => p.x);
  const ys = pts.flatMap((p) => [p.n, p.r]);
  const [x0, x1] = [Math.min(...xs), Math.max(...xs)];
  const [y0, y1] = [Math.min(...ys, 0), Math.max(...ys, 0)];
  const sx = (x) => pad + ((x - x0) / (x1 - x0 || 1)) * (width - 2 * pad);
  const sy = (y) => height - pad - ((y - y0) / (y1 - y0 || 1)) * (height - 2 * pad);
  const path = (k) => pts.map((p, i) => `${i ? "L" : "M"}${sx(p.x).toFixed(1)},${sy(p[k]).toFixed(1)}`).join(" ");
  const diffs = pts.filter((p) => p.n !== p.r).length;
  return { note: path("n"), replica: path("r"), zeroY: sy(0), zeroX: sx(0), width, height, diffs, count: pts.length };
}
