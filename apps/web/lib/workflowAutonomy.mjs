// S29B — the pure logic behind Admin → Workflows → Autonomy and the run console's unattended markers. Every control is
// decided HERE from the server's envelope and FAILS CLOSED: a control shows only when the server's own permission flag
// is exactly `true` AND the server's vocabulary lists the action — a missing, partial or malformed envelope shows none.
// Verb classes, limit names and version states come from the server's own response; nothing here invents one.

const isTrue = (v) => v === true;
const listOf = (x) => (Array.isArray(x) ? x : []);
const obj = (x) => (x && typeof x === "object" && !Array.isArray(x) ? x : {});

export const perms = (env) => obj(obj(env).permissions);
export const vocab = (env) => obj(obj(env).vocabularies);
const editable = (env) => listOf(vocab(env).editable);

/** Page-level controls. */
export function controls(env) {
  const p = perms(env);
  const org = isTrue(p.can_manage_org);
  return {
    autonomy: org && editable(env).includes("autonomy"),
    orgKill: org && editable(env).includes("kill_switch"),
    limits: org && editable(env).includes("limits"),
    shadowRuns: org && editable(env).includes("shadow_runs_required"),
    versions: isTrue(p.can_author) && editable(env).includes("version_autonomy"),
    platformKill: isTrue(p.is_super_admin) && editable(env).includes("platform_kill_switch"),
    reviewShadow: isTrue(p.can_review_shadow) && editable(env).includes("shadow_review"),
  };
}

/** May this viewer be OFFERED approve / reject on a request? Never the requester (the server re-checks through
 * services.sod — persons, not logins — and also refuses the trigger owner on an opt-in). */
export function canDecide(env, row, viewerId) {
  const r = obj(row);
  const p = perms(env);
  if (r.status !== "draft" || !isTrue(p.can_decide)) return false;
  if (!viewerId || !r.maker_id || r.maker_id === viewerId) return false;
  if (r.setting_key === "version_autonomy") return true;
  return isTrue(p.can_manage_org);
}

/** The switches as the page shows them; anything missing reads as the SAFE state (autonomy off, kill switch off). */
export function switches(env) {
  const s = obj(obj(env).switches);
  return {
    name: typeof s.org_switch === "string" ? s.org_switch : "",
    autonomy: isTrue(s.autonomy),
    orgKill: isTrue(s.org_kill_switch),
    platformKill: isTrue(s.platform_kill_switch),
    stopped: isTrue(s.org_kill_switch) || isTrue(s.platform_kill_switch),
  };
}

/** Limit rows: the server's own keys, with today's use beside the two daily ones. */
export function limitRows(env) {
  const e = obj(env);
  const lim = obj(e.limits);
  const used = obj(e.usage_today);
  return listOf(e.limit_keys).filter((k) => typeof k === "string").map((k) => ({
    key: k,
    value: Number.isInteger(lim[k]) ? lim[k] : null,
    used: k === "writes_per_day" ? (used.writes ?? null) : k === "runs_per_day" ? (used.runs ?? null) : null,
  }));
}

/** Verbs grouped by the server's own class list (classes not in the list are not shown). */
export function verbsByClass(env) {
  const e = obj(env);
  const out = {};
  for (const c of listOf(e.classes)) out[c] = [];
  for (const v of listOf(e.verbs)) {
    if (v && out[v.autonomy_class]) out[v.autonomy_class].push(v);
  }
  return out;
}

/** A run-console step's autonomy marker: null for an attended step. */
export function stepAutonomy(step) {
  const s = obj(step);
  const mode = s.autonomy_mode;
  if (mode === "unattended") {
    return { badge: "Unattended", tone: "gold", line: s.delegate_line || null, reason: null };
  }
  if (mode === "shadow") {
    const sh = obj(s.shadow);
    return { badge: "Shadow — not executed", tone: "muted", line: sh.target ? `Would write: ${sh.target}` : null,
      reason: typeof sh.would_produce === "string" ? sh.would_produce : null };
  }
  if (mode === "platform_job") return { badge: "Platform job", tone: "muted", line: null, reason: null };
  if (mode === "held_for_person") {
    return { badge: "Held for a person", tone: "gold", line: null, reason: s.autonomy_reason || s.error_detail || null };
  }
  return null;
}

/** A run's autonomy summary for the console header: null for an attended run. */
export function runAutonomy(run) {
  const r = obj(run);
  if (r.autonomy_mode === "unattended") return { label: "Ran unattended", review: null };
  if (r.autonomy_mode === "shadow") {
    return { label: "Shadow run", review: r.shadow_review_outcome === "clean" ? "Reviewed: clean"
      : r.shadow_review_outcome === "not_clean" ? "Reviewed: not clean" : "Awaiting the owner's review" };
  }
  return null;
}
