// CASH03 — the pure logic behind Household → Treasury → Ladder and Instrument plan: the obligation-matched ladder's
// timeline (rungs against the obligations they fund; targeted vs filler; indicative maturities marked) and the
// instrument plan per sleeve × account (chosen, why, net / after-tax yield, basis, as-of, runner-up). Everything is
// decided HERE from the server's envelope (`plan`, routers/cash.py `_plan_block`) and FAILS CLOSED: the one control
// (Regenerate) shows only for `permissions.can_author_policy === true` AND the plan block's own `actions` naming it
// AND not a member view. Labels (rung kinds, instrument kinds, treatments) come from the envelope's vocabularies;
// money and yields stay the server's fixed-point TEXT; nothing here computes a yield.

import { money } from "./cashPolicy.mjs";

const listOf = (x) => (Array.isArray(x) ? x : []);
const obj = (x) => (x && typeof x === "object" && !Array.isArray(x) ? x : {});

export function planBlock(env) {
  return obj(obj(env).plan);
}

export function planScopes(env) {
  return listOf(planBlock(env).scopes);
}

export function planVocab(env) {
  return obj(planBlock(env).vocabularies);
}

function labelIn(list, key) {
  const hit = listOf(list).find((x) => x && x.key === key);
  return hit ? hit.label : (key ?? "");
}

export function isMemberPlan(env) {
  return obj(env).member_view === true || planBlock(env).member_view === true;
}

/** The Regenerate control — false unless the server said so, explicitly. */
export function planControls(env) {
  const perms = obj(obj(env).permissions);
  const ok = perms.can_author_policy === true && !isMemberPlan(env) && listOf(planBlock(env).actions).includes("regenerate");
  return { regenerate: ok };
}

/** A yield as the server labelled it — "4.010% — auction high investment rate …, as of …" or the server's own text. */
export function yieldLine(y) {
  const v = obj(y);
  if (v.estimated !== true) return v.text || "not estimated";
  return `${v.yield_pct}%${v.basis ? ` — ${v.basis}` : ""}${v.as_of ? `, as of ${v.as_of}` : ""}`;
}

export function afterTaxLine(a) {
  const v = obj(a);
  if (v.estimated !== true) return v.text || "not estimated";
  return `${v.yield_pct}%${v.label ? ` (${v.label})` : ""}`;
}

/** The ladder's rungs for the table: kind / type labels from the vocabularies, indicative marked. */
export function rungRows(env, scope) {
  const voc = planVocab(env);
  const lad = obj(obj(scope).ladder);
  return listOf(lad.rungs).map((r) => ({
    key: `r${r.index}`,
    index: r.index,
    kind: r.kind,
    kindLabel: labelIn(voc.rung_kinds, r.kind),
    targeted: r.kind === "targeted",
    maturity: r.maturity,
    targetDate: r.target_date,
    type: labelIn(voc.instrument_types, r.instrument_type),
    issue: r.issue_name || null,
    cusip: r.cusip || null,
    par: money(r.par),
    covers: r.kind === "targeted"
      ? listOf(r.target_names).filter(Boolean).join(", ") || listOf(r.targets).join(", ")
      : "filler",
    funds: listOf(r.funds).length,
    indicative: r.indicative === true,
    indicativeLabel: r.indicative === true ? (r.label || "indicative maturity") : null,
    adjustment: r.adjustment || null,
    yield: yieldLine(r.yield),
  }));
}

function day(s) {
  const t = Date.parse(`${s}T00:00:00Z`);
  return Number.isFinite(t) ? t : null;
}

/** Position (0–100) of a date inside the Long-term window — for the timeline only (never a money figure). */
export function position(win, d) {
  const a = day(obj(win).from);
  const b = day(obj(win).until);
  const x = day(d);
  if (a === null || b === null || x === null || b <= a) return 0;
  return Math.max(0, Math.min(100, Math.round(((x - a) / (b - a)) * 1000) / 10));
}

/** The timeline: every rung and every Long-term obligation at its position, each obligation with the rung funding it. */
export function timeline(scope) {
  const lad = obj(obj(scope).ladder);
  const win = obj(lad.window);
  const rungs = listOf(lad.rungs).map((r) => ({
    kind: "rung", key: `r${r.index}`, index: r.index, date: r.maturity, at: position(win, r.maturity),
    targeted: r.kind === "targeted", indicative: r.indicative === true, par: money(r.par),
  }));
  const obls = listOf(lad.obligations).map((o) => ({
    kind: "obligation", key: o.key, date: o.date, at: position(win, o.date), name: o.name || o.key,
    amount: money(o.amount), large: o.large === true, rung: o.rung ?? null,
  }));
  return { from: win.from || null, until: win.until || null, rungs, obligations: obls,
    unfunded: listOf(lad.unfunded).map((u) => ({ key: u.key, date: u.date, amount: money(u.amount), text: u.text })) };
}

/** The instrument plan, one row per sleeve × account. */
export function planRows(env, scope) {
  const voc = planVocab(env);
  const ins = obj(obj(scope).instruments);
  const out = [];
  for (const s of listOf(ins.sleeves)) {
    for (const a of listOf(s.accounts)) {
      const ch = obj(a.chosen);
      const ru = obj(a.runner_up);
      out.push({
        key: `${s.key}:${a.account_id}`,
        sleeve: s.label,
        sleeveKey: s.key,
        account: a.account_label || a.account_id,
        treatment: obj(a.treatment).label || labelIn(voc.treatments, obj(a.treatment).key),
        chosen: a.chosen ? `${ch.kind_label}${ch.name ? ` — ${ch.name}` : ""}` : "nothing chosen",
        chosenIndicative: ch.indicative === true,
        why: a.why || "",
        net: a.chosen ? yieldLine(ch.net) : "—",
        afterTax: a.chosen ? afterTaxLine(ch.after_tax) : "—",
        runnerUp: a.runner_up ? `${ru.kind_label}${ru.name ? ` — ${ru.name}` : ""}` : "none",
        gap: a.gap_bp !== null && a.gap_bp !== undefined ? `${a.gap_bp} bp` : "—",
        value: a.value ? money(a.value) : (a.value_text || "—"),
        cdHurdle: a.cd_hurdle ? a.cd_hurdle.text : null,
      });
    }
  }
  return out;
}

export function blendedLine(b) {
  const v = obj(b);
  if (v.estimated !== true) return v.text || "not estimated";
  return `${v.yield_pct}%${v.from === "cash02 proxy" ? " (Treasury curve proxy — no instrument yield)" : ""}`;
}

/** Per sleeve: the instrument blend (net, after-tax) and the effective figure (instruments, else cash02's proxy). */
export function sleeveBlends(scope) {
  return listOf(obj(obj(scope).instruments).sleeves).map((s) => ({
    key: s.key, label: s.label, amount: money(s.amount),
    net: blendedLine(s.blended), afterTax: blendedLine(s.blended_after_tax), effective: blendedLine(s.effective),
    from: obj(s.effective).from || null,
  }));
}

/** The plan's household-level blends for one policy scope — shown on the policy editor too. */
export function planBlendedFor(env, scopeType, scopeId) {
  const s = planScopes(env).find((x) => x && x.scope_type === scopeType && x.scope_id === scopeId);
  if (!s) return null;
  const ins = obj(s.instruments);
  return { net: blendedLine(ins.blended), afterTax: blendedLine(ins.blended_after_tax),
    ladder: yieldLine(obj(s.ladder).yield) };
}

/** The disclosure library's after-tax label (cash.tax_estimate) and the yield label (cash.yield_estimate). */
export function taxLabel(env) {
  const l = obj(obj(obj(env).labels)["cash.tax_estimate"]);
  return l.text || null;
}

export function yieldEstimateLabel(env) {
  const l = obj(obj(obj(env).labels)["cash.yield_estimate"]);
  return l.text || null;
}

export function providerNotes(env) {
  return listOf(planBlock(env).providers).map((p) => ({ key: p.key, text: `${p.name}: ${p.status === "active" ? "on" : "switched off"}`,
    attribution: p.attribution || null }));
}
