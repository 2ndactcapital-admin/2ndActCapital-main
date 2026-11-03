// REP10 — tilts, completion, consolidation, charitable gifts, backtests and the
// hypothetical-performance policy: the pure logic behind the screens. Every
// control is decided HERE from the server's envelope and FAILS CLOSED: a
// control shows only for `=== true` on the server's own flag AND an action the
// server listed (on the envelope or on the row). A missing, partial or
// malformed envelope shows none. Every label comes from the envelope's
// vocabularies. Every backtest / projected tracking error is labelled
// HYPOTHETICAL with the server's disclosure; every tax figure is an estimate
// (or the server's "not estimated — …" reason, never a placeholder number).

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

/** Tilts page: create a set (can_write + "create"); per set: edit / submit / discard / archive need can_write and
 * the row's action; approve / reject need can_approve and the row's action (never listed for the maker). */
export function tiltControls(envelope, row = null) {
  const p = perms(envelope);
  const w = isTrue(p.can_write);
  const ap = isTrue(p.can_approve);
  if (!row) return { create: w && has(envelope, "create") && listOf(vocab(envelope).editable).length > 0 };
  return {
    edit: w && has(row, "edit"), submit: w && has(row, "submit"), discard: w && has(row, "discard"),
    archive: w && has(row, "archive"), approve: ap && has(row, "approve"), reject: ap && has(row, "reject"),
  };
}

export function completionControls(envelope) {
  const p = perms(envelope);
  const w = isTrue(p.can_write);
  return { settings: w && has(envelope, "settings") && listOf(vocab(envelope).completion_editable).length > 0,
    confirm: w && has(envelope, "confirm") };
}

export function consolidationControls(envelope, line) {
  return { consolidate: isTrue(perms(envelope).can_write) && has(line, "consolidate") };
}

export function backtestControls(envelope) {
  return { run: isTrue(perms(envelope).can_write) && has(envelope, "run") };
}

export function policyControls(envelope, row = null) {
  const p = perms(envelope);
  const m = isTrue(p.can_manage_policy);
  if (!row) return { request: m && has(envelope, "request") && listOf(vocab(envelope).policy_editable).length > 0 };
  return { approve: m && has(row, "approve"), reject: m && has(row, "reject") };
}

/** Charitable gifts: page-level (propose / add a donee) and per gift. */
export function giftControls(envelope, gift = null) {
  const p = perms(envelope);
  const w = isTrue(p.can_write);
  const ap = isTrue(p.can_approve);
  if (!gift) return { propose: w && has(envelope, "propose"), addDonee: w && has(envelope, "add_donee") };
  const a = (k) => has(gift, k);
  return {
    submit: w && a("submit"), cancel: w && a("cancel"), letter: w && a("letter"), markSent: w && a("mark_sent"),
    markReceived: w && a("mark_received"), acknowledge: w && a("acknowledge"), rebuy: w && a("rebuy"),
    approve: ap && a("approve"), reject: ap && a("reject"),
  };
}

export function pct(v, dp = 2) {
  if (v === null || v === undefined || v === "") return "—";
  const n = Number(v);
  if (!Number.isFinite(n)) return String(v);
  return `${(n * 100).toFixed(dp)}%`;
}

export function money(v) {
  if (v === null || v === undefined || v === "") return "—";
  const n = Number(v);
  if (!Number.isFinite(n)) return String(v);
  return n.toLocaleString("en-US", { style: "currency", currency: "USD" });
}

/** A projected tracking error: the HYPOTHETICAL figure, or the server's own "not estimated — needs prices (x%)". */
export function teText(te) {
  if (!te || typeof te !== "object") return "not estimated — needs prices";
  if (te.estimated === true && te.tracking_error != null) return `${pct(te.tracking_error)} (hypothetical)`;
  return te.label || "not estimated — needs prices";
}

/** An estimate with its note, or the server's reason it is not estimated. */
export function estimate(value, note) {
  if (value === null || value === undefined) return note || "not estimated";
  return `${money(value)} (estimate)`;
}

/** The disclosure every backtest surface carries — the server's text, or the staff-only line. Never empty. */
export function disclosureOf(envelope, backtest = null) {
  const d = backtest?.disclosure || envelope?.disclosure || envelope?.policy?.disclosure || envelope?.gate?.disclosure;
  return typeof d === "string" && d.trim() ? d : "no hypothetical-performance policy approved — staff use only";
}

/** Delta input → a decimal-string fraction (percentage points in, 0.01 per point out). */
export function deltaFromPoints(text) {
  const t = String(text ?? "").trim();
  if (!/^[-+]?\d+(\.\d+)?$/.test(t)) return null;
  const neg = t.startsWith("-");
  const [i, f = ""] = t.replace(/^[-+]/, "").split(".");
  const digits = (i + f).replace(/^0+(?=\d)/, "");
  const scale = f.length + 2;
  const padded = digits.padStart(scale + 1, "0");
  const out = `${padded.slice(0, padded.length - scale)}.${padded.slice(padded.length - scale)}`.replace(/\.?0+$/, "");
  return (neg && out !== "0" ? "-" : "") + (out || "0");
}
