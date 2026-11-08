// REP14 — the Section 351 exit: the pure logic behind Replication → Section 351. Every control is decided HERE from
// the server's envelope and FAILS CLOSED: a control shows only for `=== true` on the server's own permission flag AND
// an action the server listed (a missing, partial or malformed envelope shows none — never a client default). Every
// label comes from the envelope's vocabularies; every figure is shown exactly as the server states it, with the
// server's own label ("estimate — confirm with counsel").

const isTrue = (v) => v === true;
const listOf = (x) => (Array.isArray(x) ? x : []);
const obj = (x) => (x && typeof x === "object" && !Array.isArray(x) ? x : {});

export function perms(envelope) {
  return obj(obj(envelope).permissions);
}

export function vocab(envelope) {
  return obj(obj(envelope).vocabularies);
}

export function labelOf(list, key) {
  if (!Array.isArray(list)) return key ?? "";
  const hit = list.find((x) => x && x.key === key);
  return hit ? hit.label : (key ?? "");
}

const has = (env, a) => listOf(obj(env).actions).includes(a);

/** The estimate label the server attached (never a client-side default text). */
export function estimateLabel(envelope) {
  const v = vocab(envelope);
  return typeof v.label === "string" ? v.label : "";
}

/** Candidates screen: thresholds edit needs can_admin + action; plan creation / fix-up need can_write + action. */
export function candidateControls(envelope) {
  const p = perms(envelope);
  const w = isTrue(p.can_write);
  return {
    saveThresholds: isTrue(p.can_admin) && has(envelope, "edit_thresholds"),
    createPlan: w && has(envelope, "create_plan"),
    fixup: w && has(envelope, "fixup"),
  };
}

/** The thresholds envelope (GET /section351/thresholds). */
export function thresholdControls(envelope) {
  return { save: isTrue(perms(envelope).can_admin) && has(envelope, "save_thresholds") };
}

/** The sponsor-profile envelope: create / new version only for an org admin with the actions. */
export function profileControls(envelope) {
  const a = isTrue(perms(envelope).can_admin);
  return { create: a && has(envelope, "create_profile"), newVersion: a && has(envelope, "new_version") };
}

/** A plan envelope's controls. Approve / reject need can_approve AND the action (the server never lists them for the
 * maker or submitter). Per-account editing needs can_write, the "edit" action (a draft) and a non-empty editable
 * vocabulary. */
export function planControls(envelope) {
  const p = perms(envelope);
  const w = isTrue(p.can_write);
  const ap = isTrue(p.can_approve);
  const editable = listOf(vocab(envelope).editable);
  const draft = w && has(envelope, "edit") && editable.length > 0;
  return {
    edit: draft,
    addAccount: w && has(envelope, "add_account"),
    accountFields: draft,
    acknowledge: w && editable.includes("acknowledge"),
    markReady: w && has(envelope, "mark_ready"),
    reopen: w && has(envelope, "reopen"),
    submit: w && has(envelope, "submit"),
    cancel: w && has(envelope, "cancel"),
    exportBasis: w && has(envelope, "export_basis_record"),
    routeManual: w && has(envelope, "route_manual"),
    routeAdapter: w && has(envelope, "route_adapter"),
    sheet: has(envelope, "instruction_sheet"),
    confirm: w && has(envelope, "confirm_transfer"),
    steps: w && editable.includes("steps"),
    approve: ap && has(envelope, "approve"),
    reject: ap && has(envelope, "reject"),
  };
}

/** Readiness → [{scope, gate, ok, detail}] in display order (plan gates first, then each account's). A missing
 * readiness block yields one not-ok line, never an empty "all clear". */
export function gateLines(readiness, accountNames = {}) {
  const r = obj(readiness);
  if (!Array.isArray(r.plan_gates) || !Array.isArray(r.accounts)) {
    return [{ scope: "plan", gate: "readiness", ok: false, detail: "readiness not available" }];
  }
  const out = r.plan_gates.map((g) => ({ scope: "plan", gate: g.gate, ok: g.ok === true, detail: g.detail || "" }));
  for (const a of r.accounts) {
    const name = accountNames[a.account_id] || a.account_id;
    for (const g of listOf(a.gates)) out.push({ scope: name, gate: g.gate, ok: g.ok === true, detail: g.detail || "" });
  }
  return out;
}

/** True only when the server said ready AND every gate line is ok. */
export function allReady(readiness) {
  return obj(readiness).ok === true && gateLines(readiness).every((g) => g.ok);
}

const DAY = 86400000;
const days = (a, b) => Math.round((Date.parse(b) - Date.parse(a)) / DAY);

/** Timeline bars from the server's timeline: each step a bar from the previous due date to its own, as percentages
 * of the span start → max(end, start + 9 months). Steps with no due date are skipped. */
export function timelineBars(timeline) {
  const t = obj(timeline);
  const start = t.start;
  const steps = listOf(t.steps).filter((s) => s && s.due_on);
  if (!start || Number.isNaN(Date.parse(start)) || !steps.length) return { bars: [], spanDays: 0, months: [] };
  const last = Math.max(...steps.map((s) => days(start, s.due_on)));
  const spanDays = Math.max(last, 274);
  let prev = 0;
  const bars = [...steps].sort((a, b) => (a.seq ?? 0) - (b.seq ?? 0)).map((s) => {
    const end = Math.max(0, days(start, s.due_on));
    const from = Math.min(prev, end);
    prev = end;
    return { id: s.id, label: s.label, status: s.status, due_on: s.due_on,
      left: +(from / spanDays * 100).toFixed(2), width: +(Math.max(end - from, 1) / spanDays * 100).toFixed(2) };
  });
  const months = [];
  for (let m = 0; m * 30.44 <= spanDays; m += 1) months.push({ month: m, left: +((m * 30.44) / spanDays * 100).toFixed(2) });
  return { bars, spanDays, months };
}

/** The adapter route's refusal as the server sent it (a 409 with manual_route: true). */
export function adapterRefusal(errorBody) {
  const b = obj(errorBody);
  const d = obj(b.detail);
  const message = d.message || (typeof b.detail === "string" ? b.detail : b.error) || "";
  return { message, manualOffered: d.manual_route === true };
}

/** A plan account's disclosure line: the in-force version from the library, or why there is none. */
export function disclosureLine(accountState) {
  const d = obj(obj(accountState).disclosure);
  const res = obj(d.resolved);
  if (d.in_force !== true) return { inForce: false, current: false, text: "", version: null, note: d.reason || "" };
  return { inForce: true, current: d.current === true, text: res.text || "", version: res.version_no ?? null,
    versionId: res.version_id || null, title: res.title || "", note: d.current === true ? "" : (d.reason || "") };
}

/** Basis-record download links through the web proxy (never FastAPI directly). */
export function basisLinks(record) {
  const r = obj(record);
  if (!r.id) return null;
  const id = encodeURIComponent(r.id);
  return { csv: `/api/section351/basis-records/${id}/file/csv`, pdf: `/api/section351/basis-records/${id}/file/pdf` };
}

export function sheetLink(planId) {
  return planId ? `/api/section351/plans/${encodeURIComponent(planId)}/instruction-sheet` : null;
}

/** REP14 — the add-account picker: the server's `addable_accounts`, offered only with the add-account control (a
 * writer on a draft plan). Missing, malformed or empty → no picker. */
export function addableAccounts(envelope) {
  if (!planControls(envelope).addAccount) return [];
  return listOf(obj(envelope).addable_accounts).filter((a) => a && typeof a.account_id === "string" && a.account_id);
}

/** A tracker step's controls: the plan caller can write AND the step's own server-listed action. */
export function stepControls(envelope, step) {
  const w = isTrue(perms(envelope).can_write);
  const acts = listOf(obj(step).actions);
  return { done: w && acts.includes("done"), edit: w && acts.includes("edit") };
}

/** The fix-up form's sponsor-profile choices: live, active profile versions from GET /section351/sponsor-profiles. */
export function fixupProfiles(profilesEnvelope) {
  return listOf(obj(profilesEnvelope).rows).filter((r) => r && r.live === true && r.status === "active" && r.id);
}
