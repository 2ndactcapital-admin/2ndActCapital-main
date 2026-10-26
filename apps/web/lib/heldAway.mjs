// REP7 — held-away accounts and the portfolio X-ray: the pure logic behind the
// screens. Every control is decided HERE from the server's envelope and fails
// CLOSED: a control shows only for `=== true` on the server's own flag AND an
// action the server listed (on the envelope, or on the row). A held-away
// account never shows a trade control — the server lists none and this code
// adds none. Labels come from the envelope's vocabularies; every X-ray figure
// is an ESTIMATE with its inputs and as-of on hover, or the server's reason it
// is not estimated.

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

/** The held-away panel's page-level controls. */
export function panelControls(envelope) {
  const p = perms(envelope);
  const a = listOf(envelope?.actions);
  const w = isTrue(p.can_write) && envelope?.panel?.account?.is_held_away === true;
  return {
    addSource: w && a.includes("add_source"),
    manualImport: w && a.includes("manual_import"),
    fileImport: w && a.includes("file_import"),
    linkStatement: w && a.includes("link_statement"),
    statementImport: w && a.includes("statement_import"),
    requestConfirmation: w && a.includes("request_confirmation"),
    // a held-away account is never traded: there is no trade control, whatever the envelope says
    trade: false,
  };
}

/** An import row's Approve / Reject — approve_held_away AND listed on the row (never its maker: the server omits them). */
export function importControls(envelope, row) {
  const ap = isTrue(perms(envelope).can_approve);
  const a = listOf(row?.actions);
  return { approve: ap && a.includes("approve"), reject: ap && a.includes("reject") };
}

/** A confirmation row's Confirm button (manage_held_away, pending, with its task). */
export function confirmControl(envelope, c) {
  return isTrue(perms(envelope).can_write) && c?.status === "pending" && Boolean(c?.task_id);
}

/** The X-ray's "Open join request": join permission AND the server's action on the fund line. */
export function joinControl(envelope, fund) {
  return isTrue(perms(envelope).can_join) && listOf(fund?.actions).includes("open_join_request")
    && fund?.held_away !== true && fund?.join?.available === true;
}

export function runControl(envelope) {
  return isTrue(perms(envelope).can_write) && listOf(envelope?.actions).includes("run");
}

export function money(v) {
  if (v === null || v === undefined || v === "") return "—";
  const n = Number(v);
  if (!Number.isFinite(n)) return String(v);
  return n.toLocaleString("en-US", { style: "currency", currency: "USD" });
}

export function pct(v, dp = 2) {
  if (v === null || v === undefined || v === "") return "—";
  const n = Number(v);
  if (!Number.isFinite(n)) return "—";
  return `${(n * 100).toFixed(dp)}%`;
}

/** An X-ray estimate: "$X (estimate)" or the server's reason — never a bare number. */
export function estimateText(value, note) {
  if (value === null || value === undefined || value === "") return note || "not estimated";
  return `${money(value)} (estimate)`;
}

/** The hover text of an estimate: its inputs and as-of, flattened. */
export function inputsTitle(obj) {
  if (!obj || typeof obj !== "object") return "";
  const parts = [];
  if (obj.note) parts.push(obj.note);
  const inp = obj.inputs && typeof obj.inputs === "object" ? obj.inputs : {};
  for (const [k, v] of Object.entries(inp)) {
    if (v === null || v === undefined || typeof v === "object") continue;
    parts.push(`${k.replace(/_/g, " ")}: ${v}`);
  }
  return parts.join(" · ");
}

export function breakEvenText(be) {
  if (!be || typeof be !== "object") return "—";
  if (be.years === null || be.years === undefined) return be.note || "—";
  return `${be.years} years (estimate)`;
}

/** Simple bar width (0–100) for a weight string. */
export function barWidth(weight) {
  const n = Number(weight);
  if (!Number.isFinite(n) || n <= 0) return 0;
  return Math.min(100, Math.round(n * 1000) / 10);
}

/** The stale badge text for a source / account, from the server's own fields. */
export function staleText(row) {
  if (!row || row.stale !== true) return null;
  const asOf = row.as_of || row.last_as_of;
  const days = row.effective_stale_after_days ?? row.stale_after_days;
  return `Stale — data as of ${asOf || "never"} is older than ${days} day(s)`;
}

/** The proposal's held-away block: blocking dependencies with their dates. */
export function proposalStale(proposal) {
  const ha = proposal && typeof proposal === "object" ? proposal.held_away : null;
  if (!ha || typeof ha !== "object") return { blocking: false, lines: [] };
  const deps = listOf(ha.dependencies).filter((d) => d && d.stale === true);
  return { blocking: ha.blocking === true, lines: deps };
}

/** Decimal-safe: values go to the API as STRINGS (a JSON float is refused). */
export function decimalString(v) {
  if (v === null || v === undefined) return null;
  const s = String(v).trim().replace(/,/g, "");
  if (!s) return null;
  return /^-?\d+(\.\d+)?$/.test(s) ? s : null;
}

/** A manual-entry row from the form (strings; empty fields dropped). */
export function manualRow(form) {
  const out = {};
  for (const k of ["ticker", "cusip", "isin", "name", "term", "side"]) {
    if (form[k]) out[k] = String(form[k]).trim();
  }
  for (const k of ["quantity", "market_value", "cost", "price", "realized_gain_loss"]) {
    const d = decimalString(form[k]);
    if (d !== null) out[k] = d;
  }
  for (const k of ["acquired_date", "trade_date"]) {
    if (form[k]) out[k] = form[k];
  }
  return out;
}
