// BUGFIX4 — HollisMuse Olivo final report: the pure logic behind the screens it changes. Every control is decided
// HERE from the server's envelope and FAILS CLOSED (a control shows only for `=== true` on the server's own flag AND an
// action the server listed); every label comes from the envelope's vocabularies.

import { toFixedPoint } from "./fixedPoint.mjs";

const isTrue = (v) => v === true;
const listOf = (x) => (Array.isArray(x) ? x : []);

export function labelOf(list, key) {
  if (!Array.isArray(list)) return key ?? "";
  const hit = list.find((x) => x && x.key === key);
  return hit ? hit.label : (key ?? "");
}

/** A quantity as fixed-point text ("3.25E+6" → "3250000"); "—" for nothing. */
export function qty(v) {
  if (v === null || v === undefined || v === "") return "—";
  return toFixedPoint(String(v));
}

// ── Held-away entry: ONE draft per account, many lines ─────────────────────
const IDENT = ["ticker", "cusip", "isin", "sedol"];
const DEFAULT_VALUE_ONLY = ["cash", "private"];
const DEFAULT_NO_MASTER = ["digital_asset", "cash"];
const DEFAULT_NAME_OPTIONAL = ["cash", "money_market"];

function vocabList(vocab, key, fallback) {
  const v = vocab && typeof vocab === "object" ? vocab[key] : null;
  return Array.isArray(v) ? v : fallback;
}

/** A FRESH line — nothing carried over from the previous one (A25's cash line kept QQQ's CUSIP). */
export function blankLine(kind = "") {
  return { asset_kind: kind || "" };
}

/** Changing the holding type starts the line over: no field of the old type survives. */
export function changeKind(_line, kind) {
  return blankLine(kind);
}

export function isValueOnly(kind, vocab) {
  return vocabList(vocab, "value_only_kinds", DEFAULT_VALUE_ONLY).includes(kind);
}

/** ROLLUP2 Task 6 — the kinds whose Name is optional (the server's vocabulary; cash / money market by default). */
export function nameIsOptional(kind, vocab) {
  return vocabList(vocab, "name_optional_kinds", DEFAULT_NAME_OPTIONAL).includes(kind);
}

export function neverMaster(kind, vocab) {
  return vocabList(vocab, "no_master_kinds", DEFAULT_NO_MASTER).includes(kind);
}

/** The inputs a line shows for its kind. A value-only kind (cash, private) takes an AMOUNT (market value) and no
 * quantity; a kind that never resolves through the master shows no CUSIP; a listed line can carry its asset-class key. */
export function lineFields(kind, vocab, termFields = () => []) {
  if (!kind) return ["ticker", "cusip", "quantity", "market_value", "acquired_date", "cost", "taxonomy_key"];
  const base = isValueOnly(kind, vocab) ? ["name", "market_value", "cost", "taxonomy_key"]
    : ["name", "quantity", "price", "market_value", "cost", "taxonomy_key"];
  const terms = listOf(termFields(kind)).filter((f) => !(neverMaster(kind, vocab) && f === "cusip"));
  // ROLLUP2 Task 6 — cash takes an OPTIONAL name ("Coastal Bank checking"); two named cash lines stay two holdings
  const out = base;
  return [...out, ...terms.filter((f) => !out.includes(f))];
}

/** The row sent for a line: ONLY the fields its kind shows (so no stale identifier can ride along). */
export function lineRow(line, vocab, termFields = () => [], toRow = (x) => x) {
  const kind = line?.asset_kind || "";
  const allowed = new Set([...lineFields(kind, vocab, termFields), "asset_kind"]);
  const picked = {};
  for (const [k, v] of Object.entries(line || {})) {
    if (!allowed.has(k)) continue;
    if (neverMaster(kind, vocab) && IDENT.includes(k) && k !== "ticker") continue;
    if (kind === "cash" && IDENT.includes(k)) continue;
    picked[k] = v;
  }
  return toRow(picked);
}

/** A line is ready when it says what it is and how much: a value-only line its amount, any other its quantity. */
export function lineReady(line, vocab) {
  const kind = line?.asset_kind || "";
  const has = (k) => line && line[k] !== undefined && String(line[k]).trim() !== "";
  if (!kind) return (has("ticker") || has("cusip")) && has("quantity");
  const nameOptional = nameIsOptional(kind, vocab);
  if (isValueOnly(kind, vocab)) return has("market_value") && (nameOptional || has("name"));
  return has("quantity") && (nameOptional || has("name"));
}

export function lineLabel(line) {
  const k = line?.asset_kind || "";
  const id = line?.name || line?.ticker || line?.cusip || (k === "cash" ? "Cash" : "line");
  const amt = line?.market_value ? ` · ${line.market_value}` : (line?.quantity ? ` · qty ${line.quantity}` : "");
  return `${id}${amt}`;
}

/** The draft's Save control: the account takes manual entry, at least one line, an as-of, every line ready, and — when
 * the server says the draft replaces a live snapshot — the person has acknowledged it. */
export function draftSaveState({ lines, asOf, replaces, acknowledged, vocab }) {
  const ls = listOf(lines);
  if (!asOf) return { enabled: false, reason: "Enter the statement date (as-of)." };
  if (!ls.length) return { enabled: false, reason: "Add at least one line." };
  if (!ls.every((l) => lineReady(l, vocab))) return { enabled: false, reason: "Every line needs its amount or quantity." };
  if (replaces && !isTrue(acknowledged)) return { enabled: false, reason: "Confirm that this draft replaces the earlier snapshot." };
  return { enabled: true, reason: null };
}

/** An import's live state line: the server's state label, its live / superseded lines, and what replaced it. */
export function importStateText(row, vocab) {
  if (!row || typeof row !== "object" || !row.state) return null;
  const label = labelOf(vocabList(vocab, "line_states", []), row.state);
  const parts = [label];
  if (row.state === "partly_live" || row.state === "superseded" || row.state === "live") {
    parts.push(`${row.live_lines ?? 0} live, ${row.superseded_lines ?? 0} superseded`);
  }
  if (listOf(row.superseded_by).length) parts.push(`replaced by ${listOf(row.superseded_by).map((x) => String(x).slice(0, 8)).join(", ")}`);
  return parts.join(" · ");
}

// ── Entity picker: creating a record is an explicit, confirmed choice ──────
/** The picker shows "Create '<text>'" only when the caller asked for creation AND typed something; the click opens a
 * confirmation — it never creates on its own. */
export function createOffer({ allowCreate, query, loading }) {
  const text = String(query || "").trim();
  if (allowCreate !== true || !text || loading) return null;
  return { label: `Create “${text}”`, confirm: `Create a new record named “${text}”? It is added to the CRM as an incomplete profile.`, text };
}

// ── Household summary ──────────────────────────────────────────────────────
export function householdControls(envelope) {
  const p = envelope && typeof envelope === "object" ? envelope.permissions : null;
  const can = p && typeof p === "object" && p.can && typeof p.can === "object" ? p.can : {};
  return { recalculate: isTrue(can.recalculate) && listOf(envelope?.actions).includes("recalculate") };
}

export function totalLines(total) {
  if (!total || typeof total !== "object") return null;
  return { gross: total.gross, liabilities: total.liabilities, net: total.net, asOf: total.holdings_as_of || total.as_of,
    unclassified: total.unclassified_value && Number(total.unclassified_value) !== 0 ? total.unclassified_value : null,
    unvalued: Number(total.unvalued_positions) || 0 };
}

// ── Consolidate: never blank ───────────────────────────────────────────────
export function consolidateMessage({ status, loaded, error }) {
  if (!loaded) return "Loading the consolidation view…";
  if (status === 403) return "Consolidation is open to staff with replication access.";
  if (status === 404) return "This household was not found.";
  if (error) return error;
  return null;
}

// ── Private investments ────────────────────────────────────────────────────
export function privateRowControls(envelope, row) {
  const p = envelope && typeof envelope === "object" ? envelope.permissions : null;
  return { edit: isTrue(p?.can_write) && listOf(row?.actions).includes("edit"),
    projection: typeof row?.projection_path === "string" && row.projection_path.startsWith("/portfolio/commitments/") };
}

// ── Jobs switch ────────────────────────────────────────────────────────────
export function jobSwitch(envelope, job) {
  const p = envelope && typeof envelope === "object" ? envelope.permissions : null;
  if (!isTrue(p?.is_super_admin) || !listOf(envelope?.actions).includes("switch_schedule")) return null;
  if (!job || typeof job !== "object") return null;
  const on = job.trigger_active === true;
  return { label: on ? "Switch schedule off" : "Switch schedule on", next: !on,
    note: job.blocked_by_held_run ? job.blocked_note : null };
}

// ── People & Access: stub logins ───────────────────────────────────────────
export function stubLoginNotice(row) {
  if (!row || row.stub_login !== true) return null;
  const who = row.contact_email ? ` (contact email ${row.contact_email})` : "";
  return { text: `Placeholder sign-in — signs in as ${row.sign_in_id || "an unknown subject"}${who}, with no verified email. `
      + "If this is someone who already has an account, link it to their existing user.",
    canLink: listOf(row.actions).includes("link") };
}
