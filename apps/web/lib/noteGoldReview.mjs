// goldset — the gold review screen's pure logic, in a module node:test (and the
// verify) can load without React: field ordering by the server's sections,
// keyboard navigation, the correction parsers for scalar / range / list values,
// and the quote highlight. Every vocabulary (sections, member fields, enums,
// range bounds) comes from the API response; nothing here is a default list.

// Fields in the order the reviewer walks them: the server's sections, in the
// server's order (critical / economics / distribution first), each section's
// own field order. Fields the response names in no section are dropped.
export function orderedFields(payload) {
  const fields = Array.isArray(payload?.fields) ? payload.fields : [];
  const byKey = new Map(fields.map((f) => [f.key, f]));
  const out = [];
  for (const s of Array.isArray(payload?.sections) ? payload.sections : []) {
    for (const k of s.fields ?? []) if (byKey.has(k)) out.push(byKey.get(k));
  }
  return out;
}

export function sectionOf(payload, fieldKey) {
  return (payload?.sections ?? []).find((s) => (s.fields ?? []).includes(fieldKey))?.key ?? null;
}

// J / K / C / A / E / N -> an action name. Anything else (or a key typed into
// an input) -> null. Modifier keys never trigger an action.
export function keyAction(event) {
  if (!event || event.ctrlKey || event.metaKey || event.altKey) return null;
  const tag = (event.target?.tagName || "").toUpperCase();
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || event.target?.isContentEditable) return null;
  return { j: "next", k: "prev", c: "confirm", a: "absent", e: "edit", n: "nextNote" }[
    String(event.key || "").toLowerCase()
  ] ?? null;
}

export function step(index, total, dir) {
  if (!total) return 0;
  return Math.min(total - 1, Math.max(0, index + (dir === "next" ? 1 : -1)));
}

const NUM = (s) => {
  const t = String(s ?? "").trim().replace(/[,%$]/g, "");
  if (t === "") return null;
  const n = Number(t);
  return Number.isFinite(n) ? n : NaN;
};

// A scalar correction. field.kind: number | boolean | date | enum | text.
export function parseScalar(field, raw) {
  const text = String(raw ?? "").trim();
  if (text === "") return { error: "enter a value, or mark the field absent" };
  switch (field?.kind) {
    case "number": {
      const n = NUM(text);
      return Number.isFinite(n) ? { value: n } : { error: "not a number" };
    }
    case "boolean":
      return text === "true" || text === "false" ? { value: text === "true" } : { error: "true or false" };
    case "date":
      return /^\d{4}-\d{2}-\d{2}$/.test(text) ? { value: text } : { error: "use YYYY-MM-DD" };
    case "enum":
      return (field.enum ?? []).includes(text) ? { value: text } : { error: "pick one of the listed values" };
    default:
      return { value: text };
  }
}

// A range correction from separate min / max inputs plus the bound wording.
export function parseRange(field, minRaw, maxRaw, bound) {
  const min = NUM(minRaw);
  const max = NUM(maxRaw);
  if (Number.isNaN(min) || Number.isNaN(max)) return { error: "min and max must be numbers" };
  if (min === null && max === null) return { error: "enter a min, a max, or both" };
  if (min !== null && max !== null && min > max) return { error: "min exceeds max" };
  const bounds = field?.range_bounds ?? [];
  if (bound && !bounds.includes(bound)) return { error: "unknown bound" };
  const b = bound || (min === max ? "exact" : min === null ? "up_to" : max === null ? "not_less_than" : "between");
  return { value: { min, max, bound: b } };
}

// A list correction from edited rows; each row is {memberKey: raw string}.
// Member kinds come from field.members (the server's member model).
export function parseList(field, rows) {
  const members = Array.isArray(field?.members) ? field.members : [];
  if (!members.length) return { error: "this list has no member definition" };
  const list = Array.isArray(rows) ? rows : [];
  if (!list.length) return { error: "a list needs at least one row; mark the field absent instead" };
  const out = [];
  for (let i = 0; i < list.length; i += 1) {
    const row = list[i] ?? {};
    const m = {};
    for (const mf of members) {
      const raw = String(row[mf.key] ?? "").trim();
      if (raw === "") {
        if (mf.required) return { error: `row ${i + 1}: ${mf.key} is required` };
        m[mf.key] = null;
        continue;
      }
      if (mf.kind === "number") {
        const n = NUM(raw);
        if (!Number.isFinite(n)) return { error: `row ${i + 1}: ${mf.key} is not a number` };
        m[mf.key] = n;
      } else if (mf.kind === "date") {
        if (!/^\d{4}-\d{2}-\d{2}$/.test(raw)) return { error: `row ${i + 1}: ${mf.key} must be YYYY-MM-DD` };
        m[mf.key] = raw;
      } else if (mf.kind === "enum") {
        if (!(mf.enum ?? []).includes(raw)) return { error: `row ${i + 1}: ${mf.key} must be one of the listed values` };
        m[mf.key] = raw;
      } else {
        m[mf.key] = raw;
      }
    }
    out.push(m);
  }
  return { value: out };
}

// Rows to start editing a list from (a pre-filled or gold value), as strings.
export function listRows(field, value) {
  const members = field?.members ?? [];
  return (Array.isArray(value) ? value : []).map((v) =>
    Object.fromEntries(members.map((mf) => [mf.key, v?.[mf.key] == null ? "" : String(v[mf.key])])),
  );
}

// Split the document text around [start, end) for the highlight. A missing or
// out-of-range span gives one plain segment.
export function highlightSegments(text, start, end) {
  const t = typeof text === "string" ? text : "";
  if (!Number.isInteger(start) || !Number.isInteger(end) || start < 0 || end <= start || end > t.length) {
    return [{ text: t, mark: false }];
  }
  return [
    { text: t.slice(0, start), mark: false },
    { text: t.slice(start, end), mark: true },
    { text: t.slice(end), mark: false },
  ];
}

export function showValue(value) {
  if (value === null || value === undefined) return "—";
  if (Array.isArray(value)) {
    return value
      .map((v) => {
        if (typeof v !== "object" || v === null) return String(v);
        if (v.observation_date) return v.observation_date;
        const fee = v.fee_min_pct != null || v.fee_max_pct != null
          ? `, fee ${v.fee_min_pct ?? "…"}–${v.fee_max_pct ?? "…"}%` : "";
        return `${v.name ?? "?"}${v.role ? ` (${v.role}${fee})` : ""}`;
      })
      .join("; ");
  }
  if (typeof value === "object" && ("min" in value || "max" in value)) {
    return value.min === value.max ? String(value.min) : `${value.min ?? "…"} – ${value.max ?? "…"}${value.bound ? ` (${value.bound})` : ""}`;
  }
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}
