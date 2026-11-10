// CASH06 — the pure logic behind Household → Treasury → Rules (standing cash rules that DRAFT payment instructions for a
// person's approval). Everything is decided HERE from the server's reads (routers/cash_rules.py → services/cash_rules)
// and FAILS CLOSED:
//   * the screen renders only when the read carries `permissions.can_read === true` and a household;
//   * a write control renders only when the read says `permissions.can_write === true` (initiate_payment) — and a row's
//     controls only where the SERVER lists that action on it (Approve / Return never for a maker or the owner: the S30
//     floor) — with NO truthy fallback: a lost envelope shows nothing to click;
//   * a MEMBER's read (`member_view`) is read-only: their family's ACTIVE rules, the cash.rules text, last four only;
//   * the editor offers ONLY what the server's `options` read offers: sources this caller may make a rule on (the others
//     with their reasons), that source's SAME-OWNER destinations, its declared rails;
//   * labels (types, bases, conditions, frequencies, statuses, outcomes, the disclosures) come from the read.
// Money stays the server's fixed-point TEXT; percentages are text too (never binary floats).
import { lastFour, money } from "./cashPayments.mjs";

const listOf = (x) => (Array.isArray(x) ? x : []);
const obj = (x) => (x && typeof x === "object" && !Array.isArray(x) ? x : {});

export function readable(data) {
  const d = obj(data);
  return obj(d.permissions).can_read === true && !!obj(d.household).id;
}

export function isMember(data) {
  return obj(data).member_view === true;
}

/** initiate_payment (draft / submit / pause / retire) — only an explicit `true` from the server, never for a member. */
export function canWrite(data) {
  return readable(data) && !isMember(data) && obj(obj(data).permissions).can_write === true;
}

export function labelText(data, key) {
  const l = obj(obj(obj(data).labels)[key]);
  return typeof l.text === "string" && l.text ? l.text : null;
}

export function vocab(data, key) {
  return listOf(obj(obj(data).vocabularies)[key]);
}

export function labelOf(list, key) {
  const hit = listOf(list).find((x) => x && x.key === key);
  return hit ? hit.label : (key ?? "");
}

export function pctText(v) {
  return v === null || v === undefined || v === "" ? "—" : `${String(v).replace(/(\.\d*?)0+$/, "$1").replace(/\.$/, "")}%`;
}

/** "To ····5002 (40%), ····5003 (60%)" — destinations by their last four, with % or $. */
export function destinationsText(v) {
  const x = obj(v);
  if (x.rule_type === "maintain_target") {
    const sup = lastFour(obj(x.supporting).account_number_masked);
    return x.condition === "below" ? `draw from ${sup} to reach ${money(x.target_amount)}`
      : `sweep above ${money(x.target_amount)} to ${sup}`;
  }
  return listOf(x.destinations).map((d) => `${lastFour(d.account_number_masked)} (${d.pct !== undefined ? pctText(d.pct)
    : money(d.amount)})`).join(", ");
}

export function scheduleText(v) {
  const x = obj(v);
  const days = { 1: "Monday", 2: "Tuesday", 3: "Wednesday", 4: "Thursday", 5: "Friday" };
  if (x.frequency === "weekly" && x.day_of_week) return `${x.frequency_label || "Weekly"} (${days[x.day_of_week] || x.day_of_week})`;
  if (x.frequency === "monthly" && x.day_of_month) return `${x.frequency_label || "Monthly"} (day ${x.day_of_month})`;
  return x.frequency_label || x.frequency || "";
}

/** The list rows as the screen shows them (the server's own row actions; none for a member). */
export function ruleRows(data) {
  if (!readable(data)) return [];
  return listOf(obj(data).items).map((it) => {
    const v = obj(it.current);
    return {
      ruleId: it.rule_id, id: v.id, name: v.name || "", type: v.rule_type_label || v.rule_type, status: v.status_label || v.status,
      statusKey: v.status, from: lastFour(obj(v.source).account_number_masked), to: destinationsText(v),
      schedule: scheduleText(v), rail: v.rail_label || v.rail, cap: money(v.cap_per_firing), perDay: v.max_per_day,
      lastRun: it.last_run ? `${(it.last_run.at || "").slice(0, 10)} · ${it.last_run.outcome_label || it.last_run.outcome}` : null,
      nextRun: it.next_run ? it.next_run.slice(0, 16).replace("T", " ") + " UTC" : (it.next_run_label || null),
      holds: Number.isInteger(it.holds) ? it.holds : 0,
      pending: it.open_version ? `${it.open_version.status_label} (v${it.open_version.version_no})` : null,
      openId: it.open_version ? it.open_version.id : null,
      actions: isMember(data) ? [] : listOf(v.actions).filter((a) => a && a.key && a.label),
    };
  });
}

/** "New rule" — only when the server offered it AND the caller may write. */
export function newOffer(data) {
  return canWrite(data) && listOf(obj(data).actions).some((a) => a && a.key === "new");
}

// ── the editor (from the server's `options` read) ──────────────────────────────────────────────────────────────────
export function sourceChoices(opts) {
  if (!canWrite(opts)) return { eligible: [], refused: [] };
  const all = listOf(obj(opts).sources);
  return {
    eligible: all.filter((s) => s.eligible === true).map((s) => ({ key: s.id, label: lastFour(s.account_number_masked) })),
    refused: all.filter((s) => s.eligible !== true).map((s) => ({ key: s.id, label: lastFour(s.account_number_masked),
      reasons: listOf(s.reasons) })),
  };
}

function sourceOf(opts, id) {
  return listOf(obj(opts).sources).find((s) => s.id === id && s.eligible === true) || null;
}

/** Same-owner accounts of the chosen (eligible) source — the only destinations (and supporting accounts) offered. */
export function destinationChoices(opts, sourceId) {
  const s = sourceOf(opts, sourceId);
  return s ? listOf(s.destinations).map((d) => ({ key: d.id, label: lastFour(d.account_number_masked) })) : [];
}

export function railChoices(opts, sourceId) {
  const s = sourceOf(opts, sourceId);
  return s ? listOf(s.rails) : [];
}

/** Which inputs the editor shows for a rule type (the server decides the rest). */
export function fieldsFor(type, basis) {
  switch (type) {
    case "distribute":
      return { basis: true, destinations: "many", amountKind: basis === "fixed" ? "amount" : "pct",
        frequency: basis !== "pct_incoming", target: false };
    case "maintain_target":
      return { basis: false, destinations: "none", amountKind: null, frequency: true, target: true };
    case "recurring":
      return { basis: false, destinations: "one", amountKind: "amount", frequency: true, target: false };
    case "redirect_incoming":
      return { basis: false, destinations: "one", amountKind: "pct", frequency: false, target: false };
    default:
      return null;
  }
}

const CENTS = /^\d+(\.\d{1,2})?$/;
const PCT = /^\d+(\.\d{1,4})?$/;

/** The request body, or the reasons it is not ready (the server re-checks everything, with its own reasons). */
export function ruleBody(opts, form) {
  const f = obj(form);
  const errors = [];
  const fl = fieldsFor(f.rule_type, f.basis);
  if (!fl) return { ok: false, errors: ["choose a rule type"] };
  if (!String(f.name || "").trim()) errors.push("name the rule");
  if (!sourceOf(opts, f.source)) errors.push("choose a source account you may make a rule on");
  const same = destinationChoices(opts, f.source).map((d) => d.key);
  if (!railChoices(opts, f.source).some((r) => r.key === f.rail)) errors.push("choose a rail the custodian declares");
  if (!CENTS.test(String(f.cap_per_firing || "").trim()) || Number(f.cap_per_firing) <= 0) errors.push("set the per-firing cap (dollars and cents)");
  const dests = listOf(f.destinations).filter((d) => d && d.account_id);
  const body = { name: String(f.name || "").trim(), rule_type: f.rule_type, source_account_id: f.source, rail: f.rail,
    cap_per_firing: String(f.cap_per_firing || "").trim(), max_per_day: Number.parseInt(f.max_per_day || "1", 10) || 1 };
  if (fl.basis) {
    if (!["pct_balance", "fixed", "pct_incoming"].includes(f.basis)) errors.push("choose how much (basis)");
    body.basis = f.basis;
  }
  body.frequency = fl.frequency ? f.frequency : "on_incoming";
  if (fl.frequency && !["daily", "business_days", "weekly", "monthly"].includes(f.frequency)) errors.push("choose how often");
  if (body.frequency === "weekly") body.day_of_week = Number.parseInt(f.day_of_week, 10);
  if (body.frequency === "monthly") body.day_of_month = Number.parseInt(f.day_of_month, 10);
  if (fl.target) {
    if (!["below", "above"].includes(f.condition)) errors.push("choose below or above the target");
    if (!CENTS.test(String(f.target_amount || "").trim())) errors.push("set the target balance");
    if (!same.includes(f.supporting)) errors.push("choose a same-owner supporting account");
    Object.assign(body, { condition: f.condition, target_amount: String(f.target_amount || "").trim(),
      supporting_account_id: f.supporting, destinations: [] });
  } else {
    if (!dests.length) errors.push("choose a destination");
    if (fl.destinations === "one" && dests.length > 1) errors.push("this rule has one destination");
    if (dests.some((d) => !same.includes(d.account_id))) errors.push("destinations are same-owner accounts only");
    const kind = fl.amountKind;
    if (kind === "pct" && dests.some((d) => !PCT.test(String(d.value || "").trim()) || Number(d.value) <= 0)) errors.push("enter each percentage");
    if (kind === "amount" && dests.some((d) => !CENTS.test(String(d.value || "").trim()) || Number(d.value) <= 0)) errors.push("enter each amount");
    if (kind === "pct") {
      const total = dests.reduce((s, d) => s + Math.round(Number(d.value || 0) * 10000), 0);
      if (total > 100 * 10000) errors.push("the percentages add up to more than 100%");
    }
    body.destinations = dests.map((d) => ({ account_id: d.account_id, [kind]: String(d.value || "").trim() }));
  }
  if (errors.length) return { ok: false, errors };
  return { ok: true, body };
}

/** The preview: what the rule WOULD draft today (nothing is sent). */
export function previewOf(p) {
  const x = obj(obj(p).preview);
  if (!x.outcome) return null;
  return {
    outcome: x.outcome_label || x.outcome, total: money(x.total), valueDate: x.value_date || null,
    drafts: listOf(x.drafts).map((d) => ({ amount: money(d.amount), from: lastFour(obj(d.source).account_number_masked),
      to: lastFour(obj(d.destination).account_number_masked), reason: d.reason })),
    holds: listOf(x.holds).map((h) => ({ kind: h.kind_label || h.kind, reason: h.reason })),
    notes: listOf(x.notes), skipped: x.skipped || null, refusals: listOf(x.refusals),
    exampleCredit: x.example_credit ? money(x.example_credit) : null,
  };
}

// ── detail + run history ───────────────────────────────────────────────────────────────────────────────────────────
export function detailOf(data) {
  if (!readable(data)) return null;
  const r = obj(obj(data).rule);
  const v = obj(r.version);
  if (!v.id) return null;
  return {
    id: v.id, name: v.name, type: v.rule_type_label, status: v.status_label, version: v.version_no,
    from: lastFour(obj(v.source).account_number_masked), to: destinationsText(v), schedule: scheduleText(v),
    rail: v.rail_label, cap: money(v.cap_per_firing), perDay: v.max_per_day, owner: v.owner_name || null,
    makers: listOf(v.makers), approvedBy: v.approved_by_name || null, pauseReason: v.pause_reason || null,
    actions: isMember(data) ? [] : listOf(v.actions).filter((a) => a && a.key && a.label),
    versions: listOf(r.versions).map((x) => ({ id: x.id, no: x.version_no, status: x.status_label })),
  };
}

/** Engine runs → drafted instructions (each links into Payments), or the hold with its reason. */
export function historyRows(data, householdId) {
  if (!readable(data) || isMember(data)) return [];
  return listOf(obj(obj(data).rule).history).map((h) => ({
    id: h.id, on: h.evaluated_on, period: h.period_key, trigger: h.trigger_type, outcome: h.outcome_label || h.outcome,
    run: h.workflow_run_id ? `${h.workflow_run_id.slice(0, 8)}… (${h.run_status || "—"})` : null,
    reason: h.reason || null,
    instructions: listOf(h.instructions).map((p) => ({ id: p.id, label: `${money(p.amount)} · ${p.status_label || p.status || "—"}`,
      href: `/portfolio/households/${encodeURIComponent(householdId || "")}?tab=treasury&payment=${encodeURIComponent(p.id)}#treasury-payments` })),
  }));
}

/** The fields each action asks for before it is sent. */
export const ACTION_FIELDS = { submit: [], pause: ["reason"], resume: [], retire: ["reason"], approve: ["note"], reject: ["note"] };

export function actionRequest(key, fields = {}) {
  const f = obj(fields);
  const body = {};
  for (const k of ACTION_FIELDS[key] || []) {
    const v = f[k];
    if (v !== undefined && v !== null && String(v).trim() !== "") body[k] = String(v).trim();
  }
  return [key, body];
}
