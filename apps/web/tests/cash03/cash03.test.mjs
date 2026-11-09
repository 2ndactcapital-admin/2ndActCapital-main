// CASH03 — Household → Treasury → Ladder and Instrument plan: the REAL components rendered with react-dom/server from
// envelopes shaped exactly like routers/cash.py `_plan_block` returns them (services/cash_ladder.render +
// services/cash_instruments.render). The ladder shows targeted vs filler rungs against the obligations they fund,
// marks indicative maturities (and a holiday moved to the prior business day); the plan shows, per sleeve × account,
// the chosen instrument, why, net / after-tax yield with basis and as-of, the runner-up and the bp gap; a member sees
// the plan read-only with the disclosure library's labels and no CUSIP; the one control FAILS CLOSED.
import assert from "node:assert/strict";
import { register } from "node:module";
import test from "node:test";

register("../market/jsxLoader.mjs", import.meta.url);

const { createElement: h } = await import("react");
const { renderToStaticMarkup } = await import("react-dom/server");
const L = await import("../../lib/cashPlan.mjs");
const P = await import("../../components/treasury/LadderPanel.jsx");
const PP = await import("../../components/treasury/PolicyPanel.jsx");
const { LadderTimeline, RungTable, InstrumentPlan } = P;
const LadderPanel = P.default;

const text = (html) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&")
  .replace(/&quot;/g, '"').replace(/&gt;/g, ">").replace(/&lt;/g, "<").replace(/\s+/g, " ");
const render = (el) => text(renderToStaticMarkup(el));
const buttons = (el) => [...renderToStaticMarkup(el).matchAll(/<button[^>]*>([^<]*)<\/button>/g)].map((m) => m[1].trim());

const AUTHOR = { can_read: true, can_write: true, is_super_admin: false, can_author_policy: true,
  policy_permission: "manage_cash_policy" };
const READER = { ...AUTHOR, can_write: false, can_author_policy: false };
const VOC = {
  rung_kinds: [{ key: "targeted", label: "Targeted (srv)" }, { key: "filler", label: "Filler — monthly (srv)" }],
  instrument_types: [{ key: "bill", label: "Treasury bill (srv)" }, { key: "note", label: "Treasury note (srv)" },
    { key: "muni", label: "Municipal bond (srv)" }],
  treatments: [{ key: "taxable", label: "Taxable (srv)" }, { key: "tax_deferred", label: "Tax-deferred (srv)" }],
  instrument_kinds: [], editable: [], inline_editable: [],
};
const Y = (pct, basis, asOf = "2026-11-03") => ({ estimated: true, yield_pct: pct, basis, as_of: asOf, source: "auction",
  label: `${basis}, as of ${asOf}` });
const LADDER = (member = false) => ({
  window: { start_month: 12, end_month: 24, months: 12, from: "2027-11-09", until: "2028-11-09", note: null },
  rung_count: 6, threshold: "large = at least 10 % of the Long-term sleeve target, or a firm obligation of at least $250,000",
  target: "735000.00", sleeve_amount: "735000.00", par_total: "735000.00", leftover: "0.00",
  yield: { estimated: true, yield_pct: "3.973", basis: "par-weighted rung yields", as_of: "2026-11-03" },
  indicative_count: 1,
  rungs: [
    { index: 1, kind: "filler", target_date: "2027-11-09", maturity: "2027-11-04", instrument_type: "bill", matched: true,
      indicative: false, label: null, adjustment: null, cusip: member ? null : "99C030AA3", issue_name: "U.S. Treasury Bill 52-Week due 2027-11-04",
      par: "15000.00", leftover: "0.00", targets: [], funds: ["s2"], target_names: [],
      yield: Y("4.010", "auction high investment rate (bond-equivalent), auction of 2026-11-03 (Treasury Fiscal Data)") },
    { index: 2, kind: "targeted", target_date: "2028-01-14", maturity: "2027-12-31", instrument_type: "note", matched: true,
      indicative: false, label: null, adjustment: null, cusip: member ? null : "99C030AC9", issue_name: "U.S. Treasury Note 2-Year due 2027-12-31",
      par: "400000.00", leftover: "0.00", targets: ["l1"], funds: [], target_names: ["January estimated tax (srv)"],
      yield: { estimated: true, yield_pct: "3.986", basis: "FRED Treasury curve interpolated at 13.70 months (DGS1–DGS2) — its last auction is more than 35 days old", as_of: "2026-11-06" } },
    { index: 3, kind: "targeted", target_date: "2028-07-04", maturity: "2028-07-03", instrument_type: "note", matched: false,
      indicative: true, label: "indicative maturity — no matching issue",
      adjustment: "2028-07-04 is a holiday (independence day) — moved to the prior business day 2028-07-03",
      cusip: null, issue_name: null, par: "300000.00", leftover: "0.00", targets: ["l2"], funds: [], target_names: ["Pledge (srv)"],
      yield: member ? { estimated: false, text: "not estimated — index_level (index level / rate) — fred: its licence does not allow showing values to members" }
        : { estimated: true, yield_pct: "3.935", basis: "FRED Treasury curve interpolated at 19.78 months (DGS1–DGS2)", as_of: "2026-11-06" } },
  ],
  obligations: [
    { key: "s2", date: "2027-11-20", amount: "15000.00", name: "Small (srv)", large: false, rung: 1 },
    { key: "l1", date: "2028-01-14", amount: "400000.00", name: "January estimated tax (srv)", large: true, rung: 2 },
    { key: "l2", date: "2028-07-04", amount: "300000.00", name: "Pledge (srv)", large: true, rung: 3 },
  ],
  unfunded: [], notes: [], as_of: "2026-11-09",
});
const CH = (kind, kindLabel, name, net, at) => ({ kind, kind_label: kindLabel, name, net, after_tax: at, indicative: false });
const INSTRUMENTS = {
  weights_text: null,
  blended: { estimated: true, yield_pct: "4.210" }, blended_after_tax: { estimated: false, text: "not estimated — after-tax: needs a tax profile" },
  sleeves: [
    { key: "near_term", label: "Near-term (srv)", amount: "100000.00", candidate_kinds: [],
      blended: { estimated: true, yield_pct: "4.540" }, blended_after_tax: { estimated: true, yield_pct: "3.438" },
      effective: { estimated: true, yield_pct: "4.540", from: "instruments" },
      accounts: [
        { account_id: "a1", account_label: "****1001 · trust (srv)", treatment: { key: "taxable", label: "Taxable (srv)" },
          tax_profile: true, chosen: CH("treasury_mmf", "Treasury money-market fund (srv)", "Fixture Treasury Fund",
            Y("4.500", "7-day net yield (SEC Form N-MFP)", "2026-10-31"),
            { estimated: true, yield_pct: "2.664", label: "after-tax estimate — federal 0.408; state and local exempt (srv)" }),
          runner_up: CH("muni_mmf", "Municipal (tax-exempt) money-market fund (srv)", "Fixture Muni Fund",
            Y("3.100", "7-day net yield (SEC Form N-MFP)", "2026-10-31"), { estimated: true, yield_pct: "2.642" }),
          gap_bp: "2.2", basis: "after_tax", why: "Treasury money-market fund: the highest after-tax yield for a taxable account at the owner's rates (srv)",
          value: "60000.00", value_text: null,
          cd_hurdle: { estimated: true, equivalent_pct: "5.995", hurdle_bp: "150", text: "a fully taxable CD must yield 5.995 % (+150 bp) to match this Treasury after tax" },
          candidates: [] },
        { account_id: "a2", account_label: "****1002 · ira (srv)", treatment: { key: "tax_deferred", label: "Tax-deferred (srv)" },
          tax_profile: false, chosen: CH("government_mmf", "Government money-market fund (srv)", "Fixture Government Premier",
            Y("4.600", "7-day net yield (SEC Form N-MFP)", "2026-10-31"), { estimated: true, yield_pct: "4.600", label: "tax-deferred account — tax does not apply now" }),
          runner_up: null, gap_bp: null, basis: "net", why: "Government money-market fund: the highest net yield — a tax-deferred account pays no tax now (srv)",
          value: "40000.00", value_text: null, cd_hurdle: null, candidates: [] },
      ] },
    { key: "reserve", label: "Reserve (srv)", amount: "0.00", candidate_kinds: [],
      blended: { estimated: false, text: "nothing allocated" }, blended_after_tax: { estimated: false, text: "nothing allocated" },
      effective: { estimated: true, yield_pct: "4.10", from: "cash02 proxy", label: "estimate — Treasury curve proxy" }, accounts: [] },
  ],
};
const SCOPE = (member = false) => ({ scope_type: "household", scope_id: "h1", label: "The household (srv)", policy_id: "p1",
  version_no: 2, rung_count: 6, ladder: LADDER(member), instruments: INSTRUMENTS, fingerprint: "abc",
  sources: { issues: null, issues_count: 4, funds_count: 4 } });
const ENV = (over = {}) => ({
  household: { id: "h1", name: "Fixture Family" }, member_view: false, permissions: AUTHOR,
  labels: { "cash.yield_estimate": { text: "Estimates use the Treasury curve (library)" },
    "cash.tax_estimate": { text: "After-tax figures use your tax profile and are estimates (library)" } },
  plan: { scopes: [SCOPE()], member_view: false, purpose: "internal", actions: ["regenerate"], vocabularies: VOC,
    providers: [{ key: "treasury_fiscal", name: "U.S. Treasury Fiscal Data (srv)", status: "not_connected", attribution: "Source: Treasury" }] },
  ...over,
});
const MEMBER = () => ENV({ member_view: true, permissions: READER,
  plan: { scopes: [SCOPE(true)], member_view: true, purpose: "member_display", actions: [], vocabularies: VOC, providers: [] } });

test("cash03: the rung table shows targeted vs filler, the instrument, par, what it covers and each yield's basis + as-of", () => {
  const env = ENV();
  const out = render(h(RungTable, { env, scope: env.plan.scopes[0] }));
  assert.ok(out.includes("Targeted (srv)") && out.includes("Filler — monthly (srv)"), "rung kinds come from the server's vocabulary");
  assert.ok(out.includes("Treasury bill (srv)") && out.includes("Treasury note (srv)"), out);
  assert.ok(out.includes("$400,000.00") && out.includes("January estimated tax (srv)") && out.includes("+ 1 smaller"), out);
  assert.ok(out.includes("4.010% — auction high investment rate (bond-equivalent), auction of 2026-11-03 (Treasury Fiscal Data), as of 2026-11-03"), out);
  assert.ok(out.includes("99C030AA3"), "staff see the issue's CUSIP");
});

test("cash03: an indicative rung is marked, and a holiday maturity says it moved to the prior business day", () => {
  const env = ENV();
  const html = renderToStaticMarkup(h(RungTable, { env, scope: env.plan.scopes[0] }));
  const out = text(html);
  assert.equal([...html.matchAll(/data-indicative="true"/g)].length, 1, "exactly the one unmatched rung is indicative");
  assert.ok(out.includes("indicative maturity — no matching issue"), out);
  assert.ok(out.includes("2028-07-04 is a holiday (independence day) — moved to the prior business day 2028-07-03"), out);
});

test("cash03: the timeline places every rung and obligation in the Long-term window; targeted, filler and indicative differ", () => {
  const env = ENV();
  const tl = L.timeline(env.plan.scopes[0]);
  assert.equal(tl.rungs.length, 3);
  assert.equal(tl.obligations.length, 3);
  assert.deepEqual(tl.obligations.map((o) => o.rung), [1, 2, 3], "each obligation names the rung that funds it");
  assert.equal(L.position({ from: "2027-11-09", until: "2028-11-09" }, "2027-11-09"), 0);
  assert.equal(L.position({ from: "2027-11-09", until: "2028-11-09" }, "2028-11-09"), 100);
  assert.ok(tl.rungs[0].at < tl.rungs[1].at && tl.rungs[1].at < tl.rungs[2].at, "ordered by maturity");
  const html = renderToStaticMarkup(h(LadderTimeline, { scope: env.plan.scopes[0] }));
  assert.equal([...html.matchAll(/data-kind="targeted"/g)].length, 2);
  assert.equal([...html.matchAll(/data-kind="filler"/g)].length, 1);
  assert.equal([...html.matchAll(/data-kind="obligation"/g)].length, 3);
  assert.equal([...html.matchAll(/data-indicative="true"/g)].length, 1);
});

test("cash03: the instrument plan shows chosen, why, net / after-tax with basis, runner-up and the bp gap, per sleeve × account", () => {
  const env = ENV();
  const out = render(h(InstrumentPlan, { env, scope: env.plan.scopes[0] }));
  assert.ok(out.includes("Treasury money-market fund (srv) — Fixture Treasury Fund"), out);
  assert.ok(out.includes("the highest after-tax yield for a taxable account at the owner's rates (srv)"), "the why is the server's sentence");
  assert.ok(out.includes("4.500% — 7-day net yield (SEC Form N-MFP), as of 2026-10-31"), out);
  assert.ok(out.includes("2.664% (after-tax estimate — federal 0.408; state and local exempt (srv))"), out);
  assert.ok(out.includes("Municipal (tax-exempt) money-market fund (srv) — Fixture Muni Fund · 2.2 bp"), out);
  assert.ok(out.includes("Tax-deferred (srv)") && out.includes("Government money-market fund (srv) — Fixture Government Premier") && out.includes("none · —"), out);
  assert.ok(out.includes("a fully taxable CD must yield 5.995 % (+150 bp)"), "the CD hurdle line is shown for a taxable Treasury choice");
  assert.ok(out.includes("$60,000.00") && out.includes("$40,000.00"), "the allocation per account");
  assert.ok(out.includes("Near-term (srv) ($100,000.00): instruments 4.540% net, 3.438% after tax"), out);
  assert.ok(out.includes("in effect 4.10% (Treasury curve proxy — no instrument yield)"), "a sleeve without an instrument blend keeps cash02's proxy, said so");
});

test("cash03: the policy editor carries the plan's blended yields for its scope", () => {
  const env = ENV({ policy: { scopes: [{ scope_type: "household", scope_id: "h1", label: "The household (srv)", active: null }],
    member_view: false, actions: [], vocabularies: { editable: [] } } });
  const out = render(h(PP.PlanBlended, { env, scope: env.policy.scopes[0] }));
  assert.ok(out.includes("Instrument plan: blended 4.210% net, not estimated — after-tax: needs a tax profile after tax; ladder 3.973%"), out);
  assert.equal(render(h(PP.PlanBlended, { env, scope: { scope_type: "entity", scope_id: "zz" } })), "", "nothing for a scope without a plan");
});

test("cash03: staff with the authoring permission get Regenerate; every other envelope FAILS CLOSED", () => {
  assert.deepEqual(buttons(h(LadderPanel, { env: ENV() })), ["Regenerate plan"]);
  assert.equal(L.planControls(ENV()).regenerate, true);
  assert.equal(L.planControls(ENV({ permissions: READER })).regenerate, false, "no authoring permission");
  const noActions = ENV();
  noActions.plan = { ...noActions.plan, actions: [] };
  assert.equal(L.planControls(noActions).regenerate, false, "the server did not offer it");
  assert.equal(L.planControls(ENV({ permissions: undefined })).regenerate, false, "a lost permissions envelope");
  assert.equal(L.planControls(ENV({ member_view: true })).regenerate, false, "a member view never");
  assert.equal(L.planControls({}).regenerate, false, "an empty envelope");
  assert.deepEqual(buttons(h(LadderPanel, { env: noActions })), []);
});

test("cash03: a member sees their family's ladder and plan READ-ONLY, with the library's labels and no CUSIP", () => {
  const env = MEMBER();
  const el = h(LadderPanel, { env });
  const out = render(el);
  assert.deepEqual(buttons(el), [], "no control at all");
  assert.ok(out.includes("Ladder and instrument plan — The household (srv)"), out);
  assert.ok(out.includes("After-tax figures use your tax profile and are estimates (library)"), "cash.tax_estimate from the library");
  assert.ok(out.includes("Estimates use the Treasury curve (library)"), "cash.yield_estimate from the library");
  assert.ok(!out.includes("99C030"), "a member never sees a CUSIP");
  assert.ok(out.includes("its licence does not allow showing values to members"), "a licence-refused yield says why, never a number");
  assert.ok(out.includes("Read-only."));
});
