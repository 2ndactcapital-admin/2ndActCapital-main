// CASH01 — Household → Treasury: the REAL components rendered with react-dom/server from envelopes shaped exactly like
// routers/cash.py returns them. The coverage card shows months / covered through / next gap; the entity table shows
// each entity's flags; the calendar shows month totals; derived lines name their source; the lines of credit are
// shown separately; every write control FAILS CLOSED (no control without can_write === true AND the listed action AND
// an editable list), and a member envelope is read-only with the library's estimate labels.
import assert from "node:assert/strict";
import { register } from "node:module";
import test from "node:test";

register("../market/jsxLoader.mjs", import.meta.url);

const { createElement: h } = await import("react");
const { renderToStaticMarkup } = await import("react-dom/server");
const L = await import("../../lib/treasury.mjs");
const P = await import("../../components/treasury/TreasuryPanel.jsx");
const { CoverageCard, EntityTable, ObligationsCalendar, ObligationList, NotEstimated } = P;
const TreasuryPanel = P.default;

const text = (html) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&")
  .replace(/&quot;/g, '"').replace(/&gt;/g, ">").replace(/\s+/g, " ");
const render = (el) => text(renderToStaticMarkup(el));
const buttons = (el) => [...renderToStaticMarkup(el).matchAll(/<button[^>]*>([^<]*)<\/button>/g)].map((m) => m[1].trim());

const WRITER = { can_read: true, can_write: true, is_super_admin: false, read_permission: "view_portfolio",
  write_permission: "manage_portfolio" };
const READER = { ...WRITER, can_write: false };
const VOC = (editable) => ({
  source_types: [{ key: "capital_call_projected", label: "Capital call — projected (TA model) (srv)", stored: false },
    { key: "tax_estimate", label: "Estimated tax (srv)", stored: true },
    { key: "insurance_premium", label: "Insurance premium (srv)", stored: true }],
  manual_source_types: editable.length ? ["tax_estimate", "insurance_premium"] : [],
  certainties: [{ key: "firm", label: "Firm (srv)" }, { key: "estimated", label: "Estimated (srv)" }],
  flags: [{ key: "below_minimum", label: "Below its minimum coverage (srv)" },
    { key: "uncovered_90d", label: "Uncovered within 90 days (srv)" }, { key: "no_payer_cash", label: "No cash (srv)" }],
  editable, inline_editable: [],
  payers: editable.length ? [{ entity_id: "e1", display_name: "Family Trust" }] : [],
});
const COV = {
  household_id: "h1", as_of: "2026-11-09", liquid: "1500000.00", has_cash: true, monthly_outflow: "50000.00",
  months: "30.0", min_months: "6", covered_through: "2028-11-09", covered_beyond_horizon: true, next_gap: null,
  flags: [], liquid_basis: "the household's Cash & Equivalents (srv)", obligations: 4,
};
const ENTS = [
  { entity_id: "e2", display_name: "ILIT Fixture", has_cash: true, liquid: "10000.00", months: "1.2", min_months: "6",
    covered_through: "2026-12-14", covered_beyond_horizon: false,
    next_gap: { name: "Premium", amount: "60000.00", date: "2026-12-15", shortfall: "50000.00" },
    flags: ["below_minimum", "uncovered_90d"] },
  { entity_id: "e1", display_name: "Family Trust", has_cash: true, liquid: "1490000.00", months: "40.0",
    min_months: "6", covered_through: "2028-11-09", covered_beyond_horizon: true, next_gap: null, flags: [] },
];
const OBL = [
  { key: "proj:c1:1", date: "2027-02-09", amount: "250000.00", certainty: "estimated", source_type: "capital_call_projected",
    source_label: "Capital call — projected (TA model) (srv)", payer_entity_id: "e1", payer_name: "Family Trust",
    name: "Fund IV — projected call", derived: true, derived_from: "TA model (buyout) on the commitment",
    explanation: "projected by the Takahashi-Alexander model", counted: false, superseded_by: "n1", obligation_id: null },
  { key: "obl:o1:2027-04-15", date: "2027-04-15", amount: "1234567.89", certainty: "estimated", source_type: "tax_estimate",
    source_label: "Estimated tax (srv)", payer_entity_id: "e1", payer_name: "Family Trust", name: "Q1 federal estimate",
    derived: false, explanation: "recurring: FREQ=YEARLY;BYMONTH=1,4,6,9", counted: true, obligation_id: "o1" },
  { key: "prem:e2:2026-12-15", date: "2026-12-15", amount: "60000.00", certainty: "firm", source_type: "insurance_premium",
    source_label: "Insurance premium (srv)", payer_entity_id: "e2", payer_name: "ILIT Fixture", name: "ILIT — premium",
    derived: true, derived_from: "the trust's insurance policy (CRM)", explanation: "premium 60000.00 a year", counted: true },
];
const ENV = (over = {}) => ({
  household: { id: "h1", name: "Fixture Family" }, today: "2026-11-09", coverage: COV, entities: ENTS, obligations: OBL,
  calendar: [{ month: "2026-12", label: "Dec 2026", total: "60000.00", count: 1, keys: [] },
    { month: "2027-01", label: "Jan 2027", total: "0.00", count: 0, keys: [] }],
  not_estimated: [{ source_ref_id: "l1", name: "Coastal — mortgage", payer_name: "Family Trust",
    text: "not estimated — needs payment terms (srv)" }],
  lines_of_credit: [{ liability_id: "l2", name: "SBL fixture", payer_name: "Family Trust", drawn: "1000000.00",
    undrawn: "4000000.00", note: "never counted as liquid (srv)" }],
  stored: [], unmatched_notices: [], member_view: false,
  labels: { "cash.coverage_estimate": { text: "Estimate — have counsel review (library)" },
    "tax.estimate_label": { text: "estimate (library tax)" } },
  actions: ["add_obligation", "record_notice", "set_minimum", "sync_alerts"],
  permissions: WRITER, vocabularies: VOC(["obligation", "notice", "minimum"]),
  ...over,
});
const MEMBER = () => ENV({ member_view: true, actions: [], permissions: READER, vocabularies: VOC([]), stored: [] });

test("cash01: the coverage card shows months, covered-through and the next gap from the server", () => {
  const out = render(h(CoverageCard, { env: ENV() }));
  assert.ok(out.includes("30.0 months") && out.includes("Covered beyond 2028-11-09") && out.includes("$1,500,000.00"), out);
  assert.ok(out.includes("No gap within the horizon"));
  const gap = render(h(CoverageCard, { env: ENV({ coverage: { ...COV, covered_beyond_horizon: false,
    covered_through: "2027-03-14", next_gap: { name: "Caldera II call", amount: "4000000.00", date: "2027-03-15" },
    flags: ["below_minimum"] } }) }));
  assert.ok(gap.includes("Covered through 2027-03-14") && gap.includes("Next gap: Caldera II call, ~$4,000,000.00, est. 2027-03-15"), gap);
  assert.ok(gap.includes("Below its minimum coverage (srv)"), "flag labels come from the server vocabulary");
  assert.ok(out.includes("Estimate — have counsel review (library)"), "the estimate label from the library");
  assert.equal(render(h(CoverageCard, { env: { coverage: { months: null, months_text: "no obligations (srv)" } } })).includes("no obligations (srv)"), true);
});

test("cash01: the entity table flags the ILIT while the household is covered", () => {
  const out = render(h(EntityTable, { env: ENV() }));
  assert.ok(out.includes("ILIT Fixture") && out.includes("1.2 months") && out.includes("Premium, ~$60,000.00, 2026-12-15"), out);
  assert.ok(out.includes("Below its minimum coverage (srv) · Uncovered within 90 days (srv)"), out);
  assert.ok(out.includes("beyond 2028-11-09"));
  assert.deepEqual(buttons(h(EntityTable, { env: ENV() })), ["Set minimum", "Set minimum"]);
});

test("cash01: the calendar and list — derived lines name their source; a superseded projection is not counted", () => {
  const cal = render(h(ObligationsCalendar, { env: ENV() }));
  assert.ok(cal.includes("Dec 2026") && cal.includes("$60,000.00") && cal.includes("1 item") && cal.includes("0 items"), cal);
  const html = renderToStaticMarkup(h(ObligationList, { env: ENV() }));
  const out = text(html);
  assert.ok(out.includes("Derived from TA model (buyout) on the commitment") && out.includes("derived"), out);
  assert.ok(out.includes("$1,234,567.89"), "money is the server's text, digits grouped, never a Number");
  assert.ok(/line-through[^>]*>\s*<td[^>]*>2027-02-09/.test(html), "the superseded projection is struck through");
  assert.ok(out.includes("estimate (library tax)"), "a tax line carries the library's tax label");
  const only = text(renderToStaticMarkup(h(ObligationList, { env: ENV(), filters: { certainty: "firm" } })));
  assert.ok(only.includes("ILIT — premium") && !only.includes("Q1 federal estimate"), "the certainty filter narrows");
  assert.equal(L.filterObligations(ENV(), { entity: "e2" }).length, 1);
  assert.equal(L.filterObligations(ENV(), { source: "tax_estimate" }).length, 1);
  assert.deepEqual(buttons(h(ObligationList, { env: ENV() })), ["Edit", "End"], "only the stored line is editable");
});

test("cash01: the lines of credit are shown separately and not-estimated items say what they need", () => {
  const out = render(h(NotEstimated, { env: ENV() }));
  assert.ok(out.includes("not counted as liquid") && out.includes("undrawn $4,000,000.00"), out);
  assert.ok(out.includes("not estimated — needs payment terms (srv)"));
});

test("cash01: a member's view is read-only with the estimate labels; a lost envelope fails CLOSED", () => {
  const m = MEMBER();
  assert.deepEqual(buttons(h(TreasuryPanel, { initialEnvelope: m })), []);
  const out = render(h(TreasuryPanel, { initialEnvelope: m }));
  assert.ok(out.includes("Estimate — have counsel review (library)") && out.includes("ILIT Fixture"), out);
  // even a member envelope that (wrongly) claimed can_write renders nothing writable
  assert.deepEqual(buttons(h(TreasuryPanel, { initialEnvelope: { ...ENV(), member_view: true } })), []);
  for (const env of [null, {}, { permissions: null }, { ...ENV(), permissions: { can_write: "true" } },
    { ...ENV(), vocabularies: { ...VOC([]) } }, { ...ENV(), actions: [] }, { ...ENV(), vocabularies: { editable: "obligation" } }]) {
    const c = L.controls(env);
    assert.ok(Object.values(c).every((v) => v === false), JSON.stringify(env)?.slice(0, 80));
  }
  assert.ok(render(h(TreasuryPanel, { initialEnvelope: null })).includes("Treasury is open to staff"));
  const writer = buttons(h(TreasuryPanel, { initialEnvelope: ENV() }));
  assert.ok(writer.includes("Add obligation") && writer.includes("Record capital-call notice"), writer.join("|"));
});

test("cash01: the bodies keep amounts as text and drop empty optional fields", () => {
  const b = L.obligationBody({ entity_id: "e1", source_type: "tuition", name: " Fall tuition ", amount: "45000.00",
    due_date: "2027-08-15", certainty: "firm", rrule: "", notes: " " });
  assert.deepEqual(b, { entity_id: "e1", source_type: "tuition", name: "Fall tuition", amount: "45000.00",
    due_date: "2027-08-15", certainty: "firm" });
  assert.equal(typeof L.noticeBody({ entity_id: "e1", name: "Call 3", amount: "400000.00", due_date: "2027-01-10" }).amount, "string");
  assert.equal(L.money("-1234.5"), "-$1,234.5");
  assert.equal(L.money(null), "—");
});
