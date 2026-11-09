// CASH02 — the pure logic behind Household → Treasury → Cash policy: the policy editor (two boundary sliders, rung
// count, preset chips, live derived sleeves in months / $ / %, estimated yields with their labels, "draft — not
// saved" until submitted, the diff vs active), the 3-step questionnaire, the approval status and history, and the
// member's read-only view. Every control is decided HERE from the server's envelope and FAILS CLOSED: an edit / submit
// / decide control shows only for `permissions.can_author_policy === true` AND the policy block's own `actions` AND a
// non-empty `editable` list AND (for submit / decide) the server's per-version `can_submit` / `can_decide` === true.
// A member envelope never renders one. Labels (presets, sleeves, statuses, the objective scale, the disclosure texts)
// come from the envelope; money and rates stay the server's fixed-point TEXT. Nothing here computes a sleeve: the
// derived figures are always the server's (the editor asks the preview route).

const isTrue = (v) => v === true;
const listOf = (x) => (Array.isArray(x) ? x : []);
const obj = (x) => (x && typeof x === "object" && !Array.isArray(x) ? x : {});

export function block(env) {
  return obj(obj(env).policy);
}

export function pvocab(env) {
  return obj(block(env).vocabularies);
}

export function labelOf(list, key) {
  const hit = listOf(list).find((x) => x && x.key === key);
  return hit ? hit.label : (key ?? "");
}

export function scopes(env) {
  return listOf(block(env).scopes);
}

function authoring(env) {
  const b = block(env);
  return isTrue(obj(obj(env).permissions).can_author_policy) && obj(env).member_view !== true
    && b.member_view !== true && listOf(pvocab(env).editable).length > 0;
}

/** Which policy controls render for one scope entry. All false unless the server said so, explicitly. */
export function policyControls(env, scope) {
  const ok = authoring(env);
  const acts = listOf(block(env).actions);
  const open = obj(obj(scope).open);
  return {
    edit: ok && acts.includes("save_draft"),
    preview: ok && acts.includes("preview"),
    questionnaire: ok && acts.includes("save_draft") && acts.includes("preview"),
    submit: ok && acts.includes("submit") && isTrue(open.can_submit) && open.status === "draft",
    decide: ok && acts.includes("decide") && isTrue(open.can_decide) && open.status === "pending_approval",
  };
}

/** Money text with grouped digits, never through a JS Number. */
export function money(s) {
  if (s === null || s === undefined || s === "") return "—";
  const str = String(s);
  const neg = str.startsWith("-");
  const [i, f] = (neg ? str.slice(1) : str).split(".");
  const grouped = i.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return `${neg ? "-" : ""}$${grouped}${f !== undefined ? "." + f : ""}`;
}

/** A yield as the server labelled it: "4.46% — 2-year Treasury (estimate — Treasury curve proxy, as of …)" or the
 * server's own "not estimated — needs …" / "after-tax: needs a tax profile". */
export function yieldText(y) {
  const v = obj(y);
  if (v.estimated !== true) return v.text || "not estimated";
  const basis = v.basis ? ` — ${v.basis}` : "";
  return `${v.yield_pct}%${basis}${v.label ? ` (${v.label})` : ""}`;
}

export function windowText(s) {
  const w = obj(s);
  if (!w.months) return "empty";
  return `months ${w.start_month}–${w.end_month} (${w.from} to before ${w.until})`;
}

export function pctText(s) {
  const w = obj(s);
  return w.pct !== null && w.pct !== undefined ? `${w.pct}%` : (w.pct_text || "—");
}

export function sleeveRows(derived) {
  return listOf(obj(derived).sleeves).map((s) => ({
    key: s.key, label: s.label, window: windowText(s), months: s.months, amount: money(s.amount), pct: pctText(s),
    yield: yieldText(s.yield), afterTax: yieldText(s.after_tax), note: s.note || null, count: s.count,
  }));
}

export function libraryText(env, key) {
  const l = obj(obj(obj(env).labels)[key]);
  return typeof l.text === "string" && l.text ? l.text : null;
}

export const yieldLabel = (env) => libraryText(env, "cash.yield_estimate");
export const coverageLabel = (env) => libraryText(env, "cash.coverage_estimate");

export function statusLabel(env, key) {
  return labelOf(pvocab(env).statuses, key);
}

export function presetOptions(env) {
  return listOf(pvocab(env).presets);
}

export function objectiveScale(env) {
  return listOf(pvocab(env).objective_scale);
}

export function bounds(env, key) {
  const b = obj(obj(pvocab(env).bounds)[key]);
  return { min: Number.isInteger(b.min) ? b.min : 0, max: Number.isInteger(b.max) ? b.max : 0 };
}

/** The editor's starting form: the open version, else the active one, else the server's defaults. */
export function initialForm(env, scope) {
  const s = obj(scope);
  const src = obj(s.open).id ? s.open : (obj(s.active).id ? s.active : obj(pvocab(env).defaults));
  return {
    scope_type: s.scope_type || "household", entity_id: s.scope_type === "entity" ? s.scope_id : null,
    preset: src.preset || "custom", near_months: src.near_months, reserve_months: src.reserve_months,
    horizon_months: src.horizon_months, rung_count: src.rung_count, buffer_months: String(src.buffer_months ?? "0"),
    objective_score: src.objective_score, min_coverage_months: String(src.min_coverage_months ?? ""),
  };
}

/** Keep the two boundaries ordered (N ≤ R) inside the server's bounds; whole months. */
export function setBoundary(form, key, raw, env) {
  const v = Math.trunc(Number(raw));
  if (!Number.isFinite(v)) return form;
  const b = bounds(env, key);
  const val = Math.min(Math.max(v, b.min), b.max);
  const out = { ...form, [key]: val, preset: "custom" };
  if (key === "near_months" && val > (out.reserve_months ?? val)) out.reserve_months = val;
  if (key === "reserve_months" && val < (out.near_months ?? 0)) out.near_months = val;
  return out;
}

/** The request body: whole numbers as numbers, buffer / minimum as text; a non-custom preset sends no boundaries
 * (the server applies the preset). */
export function policyBody(form, extra = {}) {
  const out = { scope_type: form.scope_type, preset: form.preset };
  if (form.scope_type === "entity" && form.entity_id) out.entity_id = form.entity_id;
  if (form.preset === "custom") {
    out.near_months = Math.trunc(Number(form.near_months));
    out.reserve_months = Math.trunc(Number(form.reserve_months));
  }
  for (const k of ["horizon_months", "rung_count", "objective_score"]) {
    if (form[k] !== undefined && form[k] !== null && form[k] !== "") out[k] = Math.trunc(Number(form[k]));
  }
  for (const k of ["buffer_months", "min_coverage_months"]) {
    if (form[k] !== undefined && form[k] !== null && String(form[k]).trim() !== "") out[k] = String(form[k]).trim();
  }
  return { ...out, ...extra };
}

/** Questionnaire step 3: the recommended preset at the chosen objective (the server maps the score to N / R). */
export function recommendationBody(form, score, outflow) {
  const body = policyBody({ ...form, preset: "recommended", objective_score: score });
  if (outflow !== undefined && outflow !== null && String(outflow).trim() !== "") {
    body.questionnaire = { outflow_12m: String(outflow).trim() };
  }
  return body;
}

export function diffRows(diff) {
  return listOf(diff).map((d) => `${d.label}: ${d.from ?? "—"} → ${d.to ?? "—"}`);
}

export function makersText(version) {
  const v = obj(version);
  return listOf(v.maker_names).join(", ") || "—";
}
