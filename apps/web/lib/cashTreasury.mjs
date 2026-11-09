// CASH05 — the pure logic behind Household → Treasury's day-to-day screens: OVERVIEW (the liquid balance chart with
// transfer markers and the pending tooltip, the blended net / after-tax yield and its method note, cash01's coverage
// card), HOLDINGS (per sleeve, every ladder rung), ACTIVITY (the ledger, its filters and the detail drawer), EARNINGS
// and DOCUMENTS. Everything is decided HERE from the server's reads (routers/cash.py → services/cash_overview /
// cash_holdings / cash_activity / cash_earnings / cash_documents) and FAILS CLOSED:
//   * a screen renders only when its read carries `permissions.can_read === true` and a household;
//   * these screens are READ-ONLY: no write control exists in them at all (the reads publish `editable: []`);
//   * the actions are the server's own `actions` (links into existing screens), never a client default;
//   * a drawer link renders only when the server named BOTH the API path that opens it and the screen (`api`, `href`);
//   * labels (types, groups, ranges, the estimate texts) come from the read's vocabularies / labels.
// Money stays the server's fixed-point TEXT; dates are the server's (documents already in Eastern time).

import { money } from "./cashPolicy.mjs";
import { isoToMs, linearMap } from "./market/scales.mjs";

export { money };

const listOf = (x) => (Array.isArray(x) ? x : []);
const obj = (x) => (x && typeof x === "object" && !Array.isArray(x) ? x : {});

export function readable(data) {
  const d = obj(data);
  return obj(d.permissions).can_read === true && !!obj(d.household).id;
}

export function isMember(data) {
  return obj(data).member_view === true;
}

/** The disclosure-library text for `key` carried by this read (null when the server did not send it). */
export function labelText(data, key) {
  const l = obj(obj(obj(data).labels)[key]);
  return typeof l.text === "string" && l.text ? l.text : null;
}

export function vocabOf(data) {
  return obj(obj(data).vocabularies);
}

export function labelIn(list, key) {
  const hit = listOf(list).find((x) => x && x.key === key);
  return hit ? hit.label : (key ?? "");
}

export function entityOptions(data) {
  return listOf(vocabOf(data).entities).map((e) => ({ key: e.entity_id, label: e.display_name || "An entity" }));
}

export function rangeOptions(data) {
  return listOf(vocabOf(data).ranges);
}

export function actionLinks(data) {
  if (!readable(data)) return [];
  return listOf(obj(data).actions).filter((a) => a && a.key && a.href).map((a) => ({ key: a.key, label: a.label, href: a.href }));
}

export function pctText(p) {
  return p === null || p === undefined || p === "" ? "—" : `${p}%`;
}

// ── Overview ────────────────────────────────────────────────────────────────────────────────────────────────────
/** The chart's geometry: one SVG path (a STEP line — the balance is the latest statement until the next point),
 * point positions, marker positions. Pure. */
export function chartGeometry(points, markers, { width = 640, height = 200, pad = 28 } = {}) {
  const pts = listOf(points).map((p) => ({ ...p, ms: isoToMs(p.date), v: Number(p.value) }))
    .filter((p) => p.ms !== null && Number.isFinite(p.v));
  if (!pts.length) return { path: "", dots: [], markers: [], yTicks: [], empty: true };
  const t0 = pts[0].ms, t1 = pts[pts.length - 1].ms;
  const vs = pts.map((p) => p.v);
  let lo = Math.min(...vs), hi = Math.max(...vs);
  if (lo === hi) { lo = lo === 0 ? 0 : lo * 0.95; hi = hi === 0 ? 1 : hi * 1.05; }
  const x = linearMap(t0, t1 === t0 ? t0 + 1 : t1, pad, width - pad);
  const y = linearMap(lo, hi, height - pad, pad);
  const dots = pts.map((p) => ({ date: p.date, x: x.toPx(p.ms), y: y.toPx(p.v), value: p.value,
    pending_in: p.pending_in, pending_out: p.pending_out }));
  let path = `M${dots[0].x.toFixed(1)},${dots[0].y.toFixed(1)}`;
  for (let i = 1; i < dots.length; i += 1) {
    path += ` H${dots[i].x.toFixed(1)} V${dots[i].y.toFixed(1)}`;
  }
  const mk = listOf(markers).map((m) => {
    const ms = isoToMs(m.date);
    return ms === null ? null : { ...m, x: x.toPx(ms) };
  }).filter(Boolean);
  const yTicks = [lo, (lo + hi) / 2, hi].map((v) => ({ y: y.toPx(v), label: money(v.toFixed(2)) }));
  return { path, dots, markers: mk, yTicks, empty: false, width, height };
}

/** The tooltip for one point: value, pending in, pending out. */
export function tooltipLines(point) {
  const p = obj(point);
  if (!p.date) return [];
  return [`${p.date}`, `Balance ${money(p.value)}`, `Pending in ${money(p.pending_in)}`,
    `Pending out ${money(p.pending_out)}`];
}

export function yieldsView(data) {
  const y = obj(obj(data).yields);
  if (y.estimated !== true) {
    return { estimated: false, text: y.text || "not estimated", method: null, without: listOf(y.without_yield) };
  }
  const at = obj(y.after_tax);
  return {
    estimated: true, net: pctText(y.net_yield_pct), gross: pctText(y.gross_yield_pct),
    fee: y.fee_deducted === true ? `${pctText(y.fee_pct)} advisory fee deducted` : (y.fee_text || "advisory fee not deducted"),
    feeText: y.fee_text || null,
    afterTax: at.estimated === true ? pctText(at.yield_pct) : null,
    afterTaxText: at.estimated === true ? at.label : (at.text || null),
    method: y.method || null, covered: y.covered, total: y.total, without: listOf(y.without_yield),
  };
}

export function coverageEnv(data) {
  // cash01's card renders from {coverage, labels, vocabularies.flags}: the overview passes its own coverage through
  const d = obj(data);
  return { coverage: d.coverage || null, entities: listOf(d.coverage_entities), labels: d.labels || {},
    vocabularies: { flags: listOf(vocabOf(d).flags) } };
}

// ── Holdings ────────────────────────────────────────────────────────────────────────────────────────────────────
export function holdingGroups(data) {
  if (!readable(data)) return [];
  return listOf(obj(data).groups).map((g) => ({
    key: g.key, label: g.label || labelIn(vocabOf(data).groups, g.key), value: g.value,
    months: g.months_covered, empty: g.empty_text || null,
    holdings: listOf(g.holdings).map((h) => ({
      key: h.key, name: h.name, ticker: h.ticker || null, cusip: h.cusip || null, description: h.description,
      yield: h.yield?.estimated === true ? `${h.yield.yield_pct}%` : (h.yield?.text || "—"),
      yieldBasis: h.yield?.estimated === true ? `${h.yield.basis || ""}${h.yield.as_of ? ` · as of ${h.yield.as_of}` : ""}` : null,
      liquidity: h.liquidity, shares: h.shares, units: h.units, value: h.value, months: h.months_covered,
      prospectus: h.prospectus?.href ? { href: h.prospectus.href, text: "Prospectus" } : { href: null, text: h.prospectus?.text || "" },
      note: h.note || null,
    })),
    rungs: g.key === "long_term" ? listOf(g.rungs).map((r, i) => ({
      key: `${r.maturity || "x"}-${i}`, held: r.held === true, maturity: r.maturity, type: r.type,
      par: r.par, market: r.market_value, parVsMarket: r.par_vs_market,
      ytm: r.ytm?.estimated === true ? `${r.ytm.yield_pct}%` : (r.ytm?.text || "—"),
      ytmBasis: r.ytm?.estimated === true ? r.ytm.basis : null, serves: r.serves || "", cusip: r.cusip || null,
    })) : [],
    rungsNote: g.rungs_note || null,
  }));
}

// ── Activity ────────────────────────────────────────────────────────────────────────────────────────────────────
export function typeOptions(data) {
  return listOf(vocabOf(data).types);
}

export function activityRows(data) {
  if (!readable(data)) return [];
  const types = vocabOf(data).types;
  return listOf(obj(data).entries).map((e) => ({
    id: e.id, date: e.date, description: e.description, change: e.change !== null ? money(e.change) : (e.change_text || "—"),
    positive: e.change !== null && !String(e.change).startsWith("-"), type: labelIn(types, e.type), typeKey: e.type,
    pending: e.pending === true, status: e.status_text || "", owner: e.owner_name || "", account: e.account || "",
  }));
}

export function activityEmpty(data) {
  return readable(data) && !listOf(obj(data).entries).length ? (obj(data).empty_text || "No activity yet.") : null;
}

/** The drawer's links: only those the server named with BOTH an API path and a screen. */
export function drawerLinks(drawer) {
  return listOf(obj(drawer).links).filter((l) => l && typeof l.api === "string" && l.api.startsWith("/api/v1/")
    && typeof l.href === "string" && l.href.startsWith("/")).map((l) => ({ kind: l.kind, label: l.label, href: l.href, api: l.api }));
}

export function drawerView(drawer) {
  const d = obj(drawer);
  if (!readable(d) || !d.entry) return null;
  const e = obj(d.entry);
  return {
    title: e.description, change: e.change !== null ? money(e.change) : (e.change_text || "—"),
    type: labelIn(vocabOf(d).types, e.type), date: e.date, settlement: obj(d.settlement).text || "",
    settleDate: obj(d.settlement).settle_date || null, pending: obj(d.settlement).pending === true,
    source: `${obj(d.source).code_label || ""} · record ${obj(d.source).id || ""}`,
    account: d.account ? `${d.account.account}${d.account.custodian ? ` · ${d.account.custodian}` : ""}` : "No account",
    security: obj(d.security).name || "", owner: d.owner_name || "", links: drawerLinks(d),
    documents: listOf(d.documents), linksNote: d.links_note || null,
  };
}

// ── Earnings ────────────────────────────────────────────────────────────────────────────────────────────────────
export function periodOptions(data) {
  return listOf(vocabOf(data).periods);
}

export function earningsView(data) {
  if (!readable(data)) return null;
  const d = obj(data);
  const fee = obj(d.fee);
  return {
    period: obj(d.period), formula: d.formula || "",
    dividends: obj(d.dividends).total, funds: listOf(obj(d.dividends).funds),
    ladder: obj(d.ladder_interest).total, coupons: obj(d.ladder_interest).coupons,
    discount: obj(d.ladder_interest).realised_discount, ladderMethod: obj(d.ladder_interest).method || "",
    cash: obj(d.cash_interest).total,
    fee: fee.charged === false ? null : fee.amount, feeText: fee.text || "", feeCharged: fee.charged,
    total: d.total, reconciles: obj(d.reconciliation).reconciles === true,
    reconText: obj(d.reconciliation).text || "", ledgerFees: obj(d.reconciliation).ledger_fee_debits,
    accreting: listOf(d.accreting), missing: listOf(d.missing),
  };
}

// ── Documents ───────────────────────────────────────────────────────────────────────────────────────────────────
export function documentRows(data) {
  if (!readable(data)) return [];
  return listOf(obj(data).documents).map((x) => ({
    id: x.id, name: x.name, type: x.type_label || labelIn(vocabOf(data).types, x.type), account: x.account || "",
    date: x.date, datetime: x.datetime, open: listOf(x.links).find((l) => l.kind === "download" && l.href) || null,
    fileText: x.file_text || null,
  }));
}

export function documentsEmpty(data) {
  return readable(data) && !listOf(obj(data).documents).length ? (obj(data).empty_text || "No documents yet.") : null;
}

/** The query string of a screen's read (entity filter, range / period / type / status). */
export function readQuery(params) {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(obj(params))) {
    if (v === null || v === undefined || v === "") continue;
    if (Array.isArray(v)) v.forEach((x) => q.append(k, x));
    else q.set(k, String(v));
  }
  const s = q.toString();
  return s ? `?${s}` : "";
}
