// CASH04 — the pure logic behind Household → Treasury → Proposed trades: the household's cash proposals (a KIND of
// Replication Studio proposal) grouped by sleeve / rung with each line's reason, the early-sale warnings, the shortfall
// and the after-tax effect, plus the maturity waterfall's run history. Everything is decided HERE from the server's
// envelope (`proposals`, routers/cash.py → services/cash_proposals.household_panel) and FAILS CLOSED:
//   * the panel exists only when the envelope carries a `proposals` block AND `permissions.can_view_proposals === true`
//     AND it is not a member view (a member's envelope is unchanged — no trade detail);
//   * "Propose trades" shows only for `permissions.can_generate_proposals === true` AND the block's own `actions`
//     naming "generate"; "Adopt" only when the proposal's own server `actions` name "adopt";
//   * approve / route / the sheet are LINKS into the existing proposal and instruction screens — never a control here.
// Labels (sleeves, steps, statuses) come from the block's vocabularies; money stays the server's fixed-point TEXT.

import { money } from "./cashPolicy.mjs";

const listOf = (x) => (Array.isArray(x) ? x : []);
const obj = (x) => (x && typeof x === "object" && !Array.isArray(x) ? x : {});

export function proposalsBlock(env) {
  return obj(obj(env).proposals);
}

export function isMemberView(env) {
  return obj(env).member_view === true;
}

/** The panel renders at all only on the server's explicit word (fails closed on a lost / partial envelope). */
export function panelVisible(env) {
  const e = obj(env);
  return !isMemberView(e) && obj(e.permissions).can_view_proposals === true && e.proposals !== null
    && typeof e.proposals === "object";
}

export function panelControls(env) {
  const ok = panelVisible(env);
  const perms = obj(obj(env).permissions);
  return {
    generate: ok && perms.can_generate_proposals === true && listOf(proposalsBlock(env).actions).includes("generate"),
  };
}

export function proposalActions(p) {
  const acts = listOf(obj(p).actions);
  return { adopt: acts.includes("adopt") };
}

function labelIn(list, key) {
  const hit = listOf(list).find((x) => x && x.key === key);
  return hit ? hit.label : (key ?? "");
}

export function amountText(ln) {
  const l = obj(ln);
  if (l.quantity !== null && l.quantity !== undefined) {
    if (l.units === "par") return `${money(l.quantity)} par`;
    return `${l.quantity} shares`;
  }
  return `${money(l.notional)} notional`;
}

export function priceText(ln) {
  const l = obj(ln);
  if (l.price_kind === "limit" && l.limit_price) return `Limit ${l.limit_price} per 100 of par`;
  return "At market";
}

export function yieldText(y) {
  const v = obj(y);
  if (v.estimated !== true) return v.text || "—";
  return `${v.yield_pct}%`;
}

/** Lines grouped by sleeve, then rung — in the server's line order inside each group. */
export function groupedLines(env, p) {
  const voc = obj(proposalsBlock(env).vocabularies);
  const groups = [];
  const at = new Map();
  for (const ln of listOf(obj(p).lines)) {
    const key = `${ln.sleeve || "other"}|${ln.rung_index ?? ""}`;
    if (!at.has(key)) {
      const sleeve = labelIn(voc.sleeves, ln.sleeve) || ln.sleeve_label || "Other";
      at.set(key, { key, title: ln.rung_index ? `${sleeve} — rung ${ln.rung_index}` : sleeve, lines: [] });
      groups.push(at.get(key));
    }
    at.get(key).lines.push({
      key: `l${ln.line_no}`, lineNo: ln.line_no, side: ln.side_label || ln.side, security: ln.security || "",
      amount: amountText(ln), price: priceText(ln), reason: ln.reason || "", warning: ln.warning || null,
      step: labelIn(voc.steps, ln.step) || ln.step_label || "", maturity: ln.maturity || null,
      obligations: listOf(ln.obligations).join(", "), afterTax: yieldText(ln.est_after_tax_yield),
      limitText: ln.limit_text || null, instruction: ln.instruction_id ? `/portfolio/instructions/${ln.instruction_id}` : null,
    });
  }
  return groups;
}

export function earlySaleWarnings(p) {
  return listOf(obj(p).warnings).filter((w) => w && w.code === "early_sale").map((w) => w.message);
}

export function shortfallText(p) {
  const s = obj(p).shortfall;
  if (!s || s === "0" || s === "0.00") return null;
  return `Short by ${money(s)} — the account's cash does not cover the targets; shown, never borrowed.`;
}

export function afterTaxEffect(p) {
  const e = obj(obj(p).after_tax_effect);
  if (e.estimated !== true) return e.text || null;
  return `Buys: ${e.after_tax_yield_pct}% after tax (value-weighted), about ${money(e.annual_after_tax_income)} a year.`;
}

export function proposalRows(env) {
  if (!panelVisible(env)) return [];
  return listOf(proposalsBlock(env).proposals).map((p) => ({
    id: p.id, account: obj(p.account).account_number_masked || "", status: p.status_label || p.status,
    trigger: p.trigger_label || p.trigger, period: p.period || null, policy: p.policy_version_no ?? null,
    link: p.link || `/portfolio/proposals/${p.id}`, instructions: listOf(p.instruction_links),
    notes: listOf(p.notes), untouched: listOf(p.untouched).map((u) => u.label),
    shortfall: shortfallText(p), earlySale: earlySaleWarnings(p), afterTax: afterTaxEffect(p),
    groups: groupedLines(env, p), actions: proposalActions(p),
  }));
}

export function waterfallHistory(env) {
  if (!panelVisible(env)) return [];
  return listOf(proposalsBlock(env).waterfall_history).map((h) => ({
    key: `${h.proposal_id}`, period: h.period || "", status: h.status_label || h.status, account: h.account || "",
    created: h.created_at || "", link: `/portfolio/proposals/${h.proposal_id}`,
    maturities: listOf(h.maturities).map((m) => `${m.label} (${m.maturity})`).join(", "), earnings: h.earnings || null,
  }));
}

export function routeNotes(env) {
  return listOf(proposalsBlock(env).routes).map((r) => ({ key: r.key, label: r.label, refused: r.refusal || null }));
}
