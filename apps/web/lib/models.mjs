// REP3 — model management (Replication Studio): the pure logic behind the
// Models screens. Every control is decided HERE from the server's envelope and
// fails CLOSED: a control shows only for `=== true` on the server's own flag,
// and — for a model or version — only for an action the server listed in
// `actions` (computed for this caller and this status). A missing, partial or
// malformed envelope shows none. Every label comes from the envelope's
// vocabularies. Weights are fractions as exact decimal STRINGS from the API;
// they are shown as percentages with BigInt arithmetic, never a float.

const isTrue = (v) => v === true;

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

function actionsOf(obj) {
  return Array.isArray(obj?.actions) ? obj.actions : [];
}

/** Models list: Upload model / Load from library / New composite. */
export function listControls(envelope) {
  const w = isTrue(perms(envelope).can_write);
  const acts = actionsOf(envelope);
  return {
    upload: w && acts.includes("upload"),
    library: w && acts.includes("load_from_library"),
    composite: w && acts.includes("create_composite"),
  };
}

/** Model page: model-level controls. */
export function modelControls(envelope) {
  const p = perms(envelope);
  const acts = actionsOf(envelope);
  const w = isTrue(p.can_write);
  return {
    newDraft: w && acts.includes("new_draft"),
    uploadVersion: w && acts.includes("upload_version"),
    editDetails: w && acts.includes("edit_details"),
    retire: w && acts.includes("retire"),
    publishOrg: w && isTrue(p.can_publish_org) && acts.includes("publish_org"),
    publishPlatform: w && isTrue(p.can_publish_platform) && isTrue(p.is_super_admin) && acts.includes("publish_platform"),
  };
}

/**
 * One version's controls. `versionEnv` is {version, actions, is_maker}; the
 * permissions are the page envelope's. Approve / Reject also need
 * can_approve === true and is_maker !== true.
 */
export function versionControls(versionEnv, permissions) {
  const p = permissions && typeof permissions === "object" ? permissions : {};
  const acts = actionsOf(versionEnv);
  const w = isTrue(p.can_write);
  const decide = isTrue(p.can_approve) && versionEnv?.is_maker === false;
  return {
    edit: w && acts.includes("edit"),
    submit: w && acts.includes("submit"),
    abandon: w && acts.includes("abandon"),
    approve: decide && acts.includes("approve"),
    reject: decide && acts.includes("reject"),
  };
}

/** Library entry controls for this caller. */
export function libraryControls(envelope, entry) {
  const p = perms(envelope);
  const w = isTrue(p.can_write);
  const needs = isTrue(entry?.requires_agreement);
  const onFile = isTrue(entry?.agreement_on_file);
  const load = w && (!needs || onFile);
  return {
    copy: load,
    subscribe: load,
    confirmAgreement: w && isTrue(p.can_confirm_agreement) && needs && !onFile,
  };
}

/** Upload log: the CSV export link needs only the read flag. */
export function uploadLogControls(envelope) {
  return { exportCsv: isTrue(perms(envelope).can_read) };
}

// ── exact decimal display ──────────────────────────────────────────────────
function parseDec(s) {
  const m = String(s ?? "").trim().match(/^([+-]?)(\d*)(?:\.(\d*))?$/);
  if (!m || (m[2] === "" && (m[3] ?? "") === "")) return null;
  const frac = m[3] ?? "";
  const digits = BigInt(`${m[2] || "0"}${frac}` || "0");
  return { neg: m[1] === "-", digits, scale: frac.length };
}

/** A fraction string ("0.0450000000") as a percentage string ("4.50%"). */
export function pctText(frac, dp = 2) {
  const d = parseDec(frac);
  if (!d) return frac === null || frac === undefined ? "" : String(frac);
  // value% = digits * 100 / 10^scale; rounded half-up to dp places
  const num = d.digits * 100n * 10n ** BigInt(dp);
  const den = 10n ** BigInt(d.scale);
  let q = num / den;
  if ((num % den) * 2n >= den) q += 1n;
  const s = q.toString().padStart(dp + 1, "0");
  const out = dp > 0 ? `${s.slice(0, s.length - dp)}.${s.slice(s.length - dp)}` : s;
  return `${d.neg && q !== 0n ? "-" : ""}${out}%`;
}

/** A percent input ("4.5") → the same string, validated; null if not a number. */
export function pctInput(s) {
  const t = String(s ?? "").trim().replace(/%$/, "");
  return /^\d+(\.\d+)?$/.test(t) ? t : null;
}

/** Sum of percent strings, exact (for the sleeves editor's 100% check). */
export function sumPct(values) {
  let total = 0n;
  const scale = 10;
  for (const v of values) {
    const d = parseDec(v);
    if (!d) return null;
    total += d.digits * 10n ** BigInt(scale - Math.min(d.scale, scale));
  }
  const s = total.toString().padStart(scale + 1, "0");
  return `${s.slice(0, s.length - scale)}.${s.slice(s.length - scale)}`.replace(/\.?0+$/, "") || "0";
}

export function positionLabel(p) {
  if (!p) return "";
  if (p.resolution_state === "cash") return "Cash";
  return p.raw_ticker || p.security_name || p.raw_name || p.raw_cusip || p.raw_isin || p.raw_sedol || "";
}

export function bandText(p) {
  if (!p || (p.drift_min == null && p.drift_max == null)) return "";
  return `${p.drift_min != null ? pctText(p.drift_min) : "—"} – ${p.drift_max != null ? pctText(p.drift_max) : "—"}`;
}

const SORTS = {
  row: (p) => p.row_num ?? 0,
  name: (p) => positionLabel(p).toLowerCase(),
  weight: (p) => Number(p.target_weight ?? 0),
  resolved: (p) => (p.resolution_state === "resolved" ? 0 : p.resolution_state === "cash" ? 1 : 2),
  sleeve: (p) => (p.sleeve_label || "").toLowerCase(),
};

/** Sorted copy of positions (display only — the order never feeds a value). */
export function sortPositions(rows, key = "row", dir = "asc") {
  const f = SORTS[key] || SORTS.row;
  const out = [...(Array.isArray(rows) ? rows : [])];
  out.sort((a, b) => {
    const x = f(a);
    const y = f(b);
    return (x < y ? -1 : x > y ? 1 : 0) * (dir === "desc" ? -1 : 1);
  });
  return out;
}

/** Server messages grouped by row number (null row = whole file). */
export function messagesByRow(messages) {
  const out = new Map();
  for (const m of Array.isArray(messages) ? messages : []) {
    const k = m?.row ?? null;
    if (!out.has(k)) out.set(k, []);
    out.get(k).push(m);
  }
  return out;
}
