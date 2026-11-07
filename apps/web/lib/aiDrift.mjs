// S28 — pure helpers for Super Admin → AI → Drift monitor. Every vocabulary (rule labels, units, status labels) comes
// from the server's envelope (routers/ai_drift.py); nothing here carries a fallback list. A missing or malformed
// envelope yields no rules, no labels and NO write controls (fail closed).

/** The threshold editor renders only when the server said exactly true. */
export function canWrite(envelope) {
  return envelope?.permissions?.can_write === true;
}

/** The fields the server lets this caller edit — [] unless it published a real list (and can_write is true). */
export function editableFields(envelope) {
  const e = envelope?.vocabularies?.editable;
  return canWrite(envelope) && Array.isArray(e) ? e : [];
}

export function rules(envelope) {
  const r = envelope?.vocabularies?.rules;
  return Array.isArray(r) ? r : [];
}

export function ruleOf(envelope, key) {
  return rules(envelope).find((r) => r.key === key) || null;
}

/** The server's label for a status key; the key itself only if the server published no vocabulary. */
export function statusLabel(envelope, key) {
  const s = envelope?.vocabularies?.statuses;
  const hit = Array.isArray(s) ? s.find((x) => x.key === key) : null;
  return hit ? hit.label : "";
}

export function alertStatusLabel(envelope, key) {
  const s = envelope?.vocabularies?.alert_statuses;
  const hit = Array.isArray(s) ? s.find((x) => x.key === key) : null;
  return hit ? hit.label : "";
}

export const STATUS_TONE = { ok: "text-[#2D6A4F]", drift: "text-[#9B2335]", insufficient: "text-[#64748B]",
  disabled: "text-[#64748B]" };

const num = (v) => (v === null || v === undefined || v === "" ? null : Number(v));

export function fmtUsd(v) {
  const n = num(v);
  return n === null || Number.isNaN(n) ? "—" : `$${n.toFixed(6)}`;
}

export function fmtMs(v) {
  const n = num(v);
  return n === null || Number.isNaN(n) ? "—" : `${Math.round(n).toLocaleString("en-US")} ms`;
}

export function fmtPct(v) {
  const n = num(v);
  return n === null || Number.isNaN(n) ? "—" : `${n.toFixed(1)}%`;
}

export function fmtInt(v) {
  const n = num(v);
  return n === null || Number.isNaN(n) ? "—" : Math.round(n).toLocaleString("en-US");
}

/** An inline SVG path (polyline) for a small series; gaps (null) break the line. Pure — the render tests use it. */
export function sparkPath(values, width = 120, height = 28) {
  const pts = (values || []).map(num);
  const real = pts.filter((v) => v !== null && !Number.isNaN(v));
  if (!real.length) return "";
  const max = Math.max(...real);
  const min = Math.min(...real, 0);
  const span = max - min || 1;
  const step = pts.length > 1 ? width / (pts.length - 1) : 0;
  let d = "";
  let pen = false;
  pts.forEach((v, i) => {
    if (v === null || Number.isNaN(v)) {
      pen = false;
      return;
    }
    const x = (i * step).toFixed(1);
    const y = (height - ((v - min) / span) * height).toFixed(1);
    d += `${pen ? "L" : "M"}${x},${y}`;
    pen = true;
  });
  return d;
}

/** Client-side hint only (the server decides): the edit the editor would send, or an error sentence. */
export function thresholdDraft(rule, draft) {
  if (!rule) return { error: "Unknown rule" };
  const out = { rule_key: rule.key, task_type: draft.task_type || null, enabled: draft.enabled === true,
    note: (draft.note || "").trim() };
  if (!out.note) return { error: "Say why the threshold is changing" };
  if (rule.has_threshold) {
    const t = String(draft.threshold ?? "").trim();
    if (!/^\d+(\.\d+)?$/.test(t)) return { error: "The threshold is a number, zero or more" };
    out.threshold = t;   // a string: the server parses it as an exact Decimal, never a float
  } else {
    out.threshold = null;
  }
  for (const k of ["min_recent", "min_baseline"]) {
    const v = String(draft[k] ?? "").trim();
    if (!/^\d+$/.test(v)) return { error: `${k === "min_recent" ? "Minimum recent" : "Minimum baseline"} is a whole number` };
    out[k] = Number(v);
  }
  if (out.min_recent < 1) return { error: "Minimum recent is at least 1" };
  return { body: out };
}
