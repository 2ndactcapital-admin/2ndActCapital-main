// S30 — the pure logic behind Admin → Segregation of duties. Every control is decided HERE from the server's envelope
// and FAILS CLOSED: a control shows only when the server's own permission flag is exactly `true` AND the server's
// vocabulary lists the action — a missing, partial or malformed envelope shows none (never a client default). Every
// label (item types, duties, rule kinds) comes from the server's own rows; nothing here invents one.

const isTrue = (v) => v === true;
const listOf = (x) => (Array.isArray(x) ? x : []);
const obj = (x) => (x && typeof x === "object" && !Array.isArray(x) ? x : {});

export const perms = (env) => obj(obj(env).permissions);
export const vocab = (env) => obj(obj(env).vocabularies);
const editable = (env) => listOf(vocab(env).editable);

/** Page-level controls: draft an org rule, an org policy, a platform rule. */
export function controls(env) {
  const p = perms(env);
  const w = isTrue(p.can_write);
  return {
    draftOrgRule: w && editable(env).includes("org_rule"),
    draftPolicy: w && editable(env).includes("policy"),
    draftPlatform: w && isTrue(p.is_super_admin) && editable(env).includes("platform_rule"),
  };
}

/** May this viewer be OFFERED approve / reject on a draft rule version? Never the drafter (the server re-checks
 * through services.sod, persons not logins); a platform draft only for a Super Admin. */
export function canDecide(env, rule, viewerId) {
  const r = obj(rule);
  const p = perms(env);
  if (r.status !== "draft" || !isTrue(p.can_write)) return false;
  if (!viewerId || !r.drafted_by || r.drafted_by === viewerId) return false;
  if (r.org_id) return editable(env).includes("org_rule") || editable(env).includes("policy");
  return isTrue(p.is_super_admin) && editable(env).includes("platform_rule");
}

export function itemLabel(env, key) {
  const t = listOf(obj(env).item_types).find((x) => x && x.item_type === key);
  return t ? t.label : key ?? "";
}

export function dutyLabel(env, key) {
  const d = listOf(obj(env).duties).find((x) => x && x.duty_key === key);
  return d ? d.label : key ?? "";
}

/** Rules grouped by item type, current versions first; history (retired / rejected) kept, flagged. */
export function rulesByItem(env) {
  const out = {};
  for (const r of listOf(obj(env).rules)) {
    if (!r || !r.item_type) continue;
    (out[r.item_type] ||= []).push({ ...r, current: r.status === "active" && !r.valid_to, history: ["retired", "rejected"].includes(r.status) });
  }
  return out;
}

export const REPORT_NAMES = ["duties", "item_types", "rules", "conflicts", "self_approvals", "refusals"];

/** The CSV link for a report — only for a report the server lists. */
export function csvHref(env, name) {
  if (!listOf(vocab(env).reports).includes(name)) return null;
  return `/api/sod/reports/${encodeURIComponent(name)}?format=csv`;
}

export function floorNotEnforced(env) {
  return listOf(obj(env).item_types).filter((t) => t && t.policy && t.policy.enforce_floor === false);
}
