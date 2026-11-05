// PORTENTRY2 — pure helpers behind the onboarding fixes HollisMuse hit on the Olivo family (2026-11-05).
// Every option list and label comes from an API envelope; a lost envelope renders no write control (fail closed).

// ── Dates (MUSE-OLIVO-B11-01 / B11-03) ──────────────────────────────────────────────────────────────────────
export const YEAR_MIN = 1800;
export const YEAR_MAX = 2200;

function pad(n) {
  return String(n).padStart(2, "0");
}

/**
 * Typed or pasted text → { iso, error }. Accepts ISO (2019-03-07, 2019/03/07), US (03/07/2019, 3-7-2019) and a
 * full ISO timestamp (its date part). Never "mangles" a year: a year outside 1800–2200, a two-digit year or an
 * impossible day is refused with a message and nothing is emitted. Blank → { iso: "", error: null }.
 */
export function parseDateInput(text) {
  const s = String(text ?? "").trim();
  if (!s) return { iso: "", error: null };
  let y;
  let m;
  let d;
  let hit = s.match(/^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?:[T ].*)?$/);
  if (hit) {
    [y, m, d] = [hit[1], hit[2], hit[3]].map(Number);
  } else if ((hit = s.match(/^(\d{1,2})[-/.](\d{1,2})[-/.](\d+)$/))) {
    if (hit[3].length !== 4) {
      return { iso: "", error: "Type the year in full (four digits), e.g. 03/07/2019 or 2019-03-07" };
    }
    [m, d, y] = [hit[1], hit[2], hit[3]].map(Number);
  } else if ((hit = s.match(/^(\d+)[-/.](\d{1,2})[-/.](\d{1,2})$/))) {
    return { iso: "", error: `Year ${hit[1]} is outside ${YEAR_MIN}–${YEAR_MAX}` };
  } else {
    return { iso: "", error: "Use a date like 2019-03-07 or 03/07/2019" };
  }
  if (y < YEAR_MIN || y > YEAR_MAX) return { iso: "", error: `Year ${y} is outside ${YEAR_MIN}–${YEAR_MAX}` };
  if (m < 1 || m > 12) return { iso: "", error: `Month ${m} does not exist` };
  const dt = new Date(Date.UTC(y, m - 1, d));
  if (dt.getUTCFullYear() !== y || dt.getUTCMonth() !== m - 1 || dt.getUTCDate() !== d) {
    return { iso: "", error: `${y}-${pad(m)}-${pad(d)} is not a real date` };
  }
  return { iso: `${y}-${pad(m)}-${pad(d)}`, error: null };
}

// ── Seasonal addresses (MUSE-OLIVO-C.check) ─────────────────────────────────────────────────────────────────
export const MONTHS = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** Is `month` (1–12) inside the season from → to, inclusive; a season may wrap the year (Nov–Apr). */
export function inSeason(from, to, month) {
  if (!from || !to || !month) return false;
  return from <= to ? month >= from && month <= to : month >= from || month <= to;
}

/**
 * The address list's season line for `date` (a Date, or YYYY-MM-DD): the seasonal address whose months hold today is
 * "Current · Nov–Apr"; every other seasonal one is "Seasonal, May–Oct". A non-seasonal address has no season line.
 */
export function seasonalStatus(address, date) {
  if (!address?.is_seasonal || !address.season_from_month || !address.season_to_month) return null;
  const month = typeof date === "string" ? Number(date.slice(5, 7)) : (date || new Date()).getMonth() + 1;
  const months = `${MONTHS[address.season_from_month]}–${MONTHS[address.season_to_month]}`;
  const current = inSeason(address.season_from_month, address.season_to_month, month);
  return { current, months, label: current ? `Current · ${months}` : `Seasonal, ${months}` };
}

// ── Percentages (MUSE-OLIVO-F-03) ───────────────────────────────────────────────────────────────────────────
/** A 0–100 percentage string/number → "60.0%". */
export function formatPct(value, digits = 1) {
  if (value === null || value === undefined || value === "") return "—";
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  return `${n.toFixed(digits)}%`;
}

/**
 * GET /entities/{id}/lookthrough returns each HOLDING's effective share as a 0–1 FRACTION ("0.600000");
 * the tree's owners carry `effective_pct` already in 0–100. Normalise the holdings to 0–100 so one map holds one
 * unit — the graph printed the fraction with a % sign ("0.6% eff.").
 */
export function lookthroughPercentMap(items) {
  const out = {};
  for (const it of Array.isArray(items) ? items : []) {
    if (!it?.entity_id || it.effective_pct === null || it.effective_pct === undefined) continue;
    const n = Number(it.effective_pct);
    if (Number.isFinite(n)) out[it.entity_id] = n * 100;
  }
  return out;
}

// ── Ownership views (MUSE-OLIVO-F-01 / F-02) ────────────────────────────────────────────────────────────────
/** The edges below `rootId` in a {nodes, edges} graph: direct only (depth 1) unless look-through is on. */
export function ownsRows(graph, rootId, { lookThrough = false } = {}) {
  const edges = Array.isArray(graph?.edges) ? graph.edges : [];
  const names = Object.fromEntries((graph?.nodes || []).map((n) => [n.id, n]));
  const out = [];
  const seen = new Set();
  const walk = (id, share, depth, path) => {
    for (const e of edges.filter((x) => x.parent_id === id)) {
      if (path.includes(e.child_id)) continue;
      const eff = share * (Number(e.ownership_pct) || 0) / 100;
      if (depth === 1 || lookThrough) {
        const key = `${e.child_id}`;
        if (!seen.has(key)) {
          seen.add(key);
          out.push({ id: e.child_id, display_name: names[e.child_id]?.display_name || "—", depth,
            ownership_pct: depth === 1 ? Number(e.ownership_pct) : null, effective_pct: eff * 100,
            via: depth === 1 ? null : names[id]?.display_name || null });
        } else {
          const row = out.find((r) => r.id === key);
          row.effective_pct += eff * 100;
        }
      }
      if (lookThrough) walk(e.child_id, eff, depth + 1, [...path, e.child_id]);
    }
  };
  walk(rootId, 1, 1, [rootId]);
  return out;
}

/** The owners of `rootId` (direct), one row per owner. */
export function ownedByRows(graph, rootId) {
  const edges = Array.isArray(graph?.edges) ? graph.edges : [];
  const names = Object.fromEntries((graph?.nodes || []).map((n) => [n.id, n]));
  return edges.filter((e) => e.child_id === rootId)
    .map((e) => ({ id: e.parent_id, display_name: names[e.parent_id]?.display_name || "—",
      ownership_pct: Number(e.ownership_pct) }))
    .sort((a, b) => a.display_name.localeCompare(b.display_name));
}

// ── Permission-true rendering (MUSE-OLIVO-PERS-01) ──────────────────────────────────────────────────────────
/** A write control for `key` renders only when the envelope says so — never a truthy fallback. */
export function canEdit(envelope, key) {
  if (envelope?.permissions?.can_write !== true) return false;
  const can = envelope?.permissions?.can;
  if (can && typeof can === "object") return can[key] === true;
  const ed = envelope?.vocabularies?.editable;
  return Array.isArray(ed) && ed.includes(key);
}

/** The Ownership tab's Add / Edit / Remove (GET /entities/{id}/ownership's own envelope). */
export function canEditOwnership(ownershipEnvelope) {
  return ownershipEnvelope?.permissions?.can_write === true;
}

// ── Picker (MUSE-OLIVO-E4-01 / D19-01 / D17-01) ─────────────────────────────────────────────────────────────
/** Family relationships (the envelope's own "family" group) and grantor look up deceased / inactive records. */
export function includeInactiveDefault(option) {
  return Boolean(option && (option.group === "family" || option.key === "grantor"));
}

/** The picker's search URL: every word is sent; inactive / deceased only when asked. */
export function pickerSearchParams({ q, entityTypes = [], excludeId = null, page = 1, pageSize = 20,
  includeInactive = false }) {
  const p = new URLSearchParams();
  if (q) p.set("q", String(q).trim());
  for (const t of entityTypes || []) p.append("entity_type", t);
  if (excludeId) p.append("exclude_ids", excludeId);
  p.set("page", String(page));
  p.set("page_size", String(pageSize));
  if (includeInactive) p.set("include_inactive", "true");
  return p;
}

const UUID_RX = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** What the picker box shows for a chosen value: its name — never a bare id. */
export function pickerDisplay(value) {
  if (!value) return "";
  const name = value.display_name || value.label || value.name || "";
  if (name && !UUID_RX.test(name)) return name;
  return "";
}

// ── Households and accounts (MUSE-OLIVO-H-01..05, G-02) ─────────────────────────────────────────────────────
/**
 * The Add account "Owner" choices: every member (person or legal entity), then the legal entities a member owns
 * that are not members yet (choosing one links it as a member in the same step).
 */
export function ownerOptions(detail) {
  const members = (Array.isArray(detail?.members) ? detail.members : []).map((m) => ({
    value: m.id, label: m.display_name, entity_type: m.entity_type, addAsMember: false, group: "members" }));
  const candidates = (Array.isArray(detail?.owner_candidates) ? detail.owner_candidates : []).map((c) => ({
    value: c.id, label: `${c.display_name} (owned by ${c.owned_by_name || "a member"} — adds it as a member)`,
    entity_type: c.entity_type, addAsMember: true, group: "candidates" }));
  return [...members, ...candidates];
}

/** The DVP ("another custodian") fields still missing — the same rule as services.household_admin.dvp_missing. */
export function dvpMissing(custody, receivingName, participantNumber) {
  if (custody !== "other_custodian") return [];
  const out = [];
  if (!String(receivingName || "").trim()) out.push("receiving custodian");
  if (!String(participantNumber || "").trim()) out.push("DTC / participant number");
  return out;
}

export function dvpReason(missing) {
  return missing.length
    ? `Another custodian (settled DVP) needs the ${missing.join(" and the ")} — the receiving side of every ` +
      "delivery-versus-payment settlement."
    : null;
}

/** The account card line: mask · custodian · type · tax · billable · discretionary · held away. */
export function accountLine(a) {
  if (!a) return "";
  const parts = [a.account_number_masked, a.custodian_code];
  parts.push(a.registration_type_label || "Type not set");
  parts.push(a.tax_status_label || "Tax status not set");
  parts.push(a.is_billable ? "billable" : "not billable");
  if (a.is_discretionary) parts.push("discretionary");
  if (a.service_model) parts.push(a.service_model);
  if (a.is_held_away) parts.push("held away");
  if (a.owner_name) parts.push(`owner ${a.owner_name}`);
  return parts.filter(Boolean).join(" · ");
}

/** The list row's live counts after a detail (re)load — no page reload needed (H-05). */
export function withCounts(rows, detail) {
  return (Array.isArray(rows) ? rows : []).map((r) => (detail && r.id === detail.id
    ? { ...r, members: (detail.members || []).length, accounts: (detail.accounts || []).length }
    : r));
}

/** Billable default for Add account: off for a demo household, on otherwise. */
export function billableDefault(detail) {
  return detail?.is_demo !== true;
}
