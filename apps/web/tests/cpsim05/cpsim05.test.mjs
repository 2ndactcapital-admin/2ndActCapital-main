// CPSIM05 — the SIMULATED FIX counterparty on three screens, rendered with react-dom/server from envelopes shaped
// exactly like the API returns them: the proposal page's simulated-execution P/L (services/fix_sim_pl.proposal_pl),
// the instruction page's FIX exchange (services/execution.fix_exchange) and Admin → Counterparties → FIX's message
// log (GET /admin/counterparties/{id}/fix/messages). Every number / message is labelled SIMULATED; each panel renders
// NOTHING when the server sends no data (fails closed — no client-side default); none of them carries a button.
import assert from "node:assert/strict";
import { register } from "node:module";
import test from "node:test";

register("../market/jsxLoader.mjs", import.meta.url);

const { createElement: h } = await import("react");
const { renderToStaticMarkup } = await import("react-dom/server");
const P = await import("../../components/proposals/ProposalDetail.jsx");
const D = await import("../../components/execution/InstructionDetail.jsx");
const C = await import("../../components/counterparties/Counterparties.jsx");

const render = (el) => renderToStaticMarkup(el);
const text = (html) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&")
  .replace(/&quot;/g, '"').replace(/\s+/g, " ");
const count = (html, rx) => (html.match(rx) || []).length;

const PL = {
  label: "SIMULATED", simulated: true, method: "realized = the tax engine's loss-bank entries for these fills",
  trades: [
    { instruction_id: "i-sell", security: "Fixture Equity", side: "sell", status: "reconciled", order_id: "sim-o-1",
      filled_quantity: "10", average_price: "101.25", proceeds: "1012.50", cost: null, realized_st: "-40.00",
      realized_lt: "212.50", realized_note: null, label: "SIMULATED" },
    { instruction_id: "i-buy", security: "Fixture Fund", side: "buy", status: "reconciled", order_id: "sim-o-2",
      filled_quantity: "4", average_price: "50", proceeds: null, cost: "200.00", realized_st: null, realized_lt: null,
      realized_note: null, label: "SIMULATED" },
  ],
  totals: { proceeds: "1012.50", cost: "200.00", realized_st: "-40.00", realized_lt: "212.50", realized: "172.50",
    label: "SIMULATED" },
  unrealized: { total: "88.00", lot_source: "hollisworks", as_of: "2026-11-11", label: "SIMULATED", lots: [] },
};
const proposalEnv = (extra = {}) => ({
  proposal: { id: "p1", status: "approved", trigger: "manual", tasks: [], warnings: [], trades: [],
    instructions: [{ id: "i-sell", side: "sell", quantity: "10", status: "reconciled", routed_via: "fix_sim",
      sent_channel: "fix_sim" }], ...extra },
  permissions: { can_read: true, can_write: false, can_approve: false }, actions: [], vocabularies: {},
});

test("proposal P/L: per trade fill, proceeds / cost, realized ST / LT and the totals — every number SIMULATED", () => {
  const html = render(h(P.SimulatedFixPL, { envelope: proposalEnv({ fix_sim_pl: PL }) }));
  const t = text(html);
  assert.match(t, /Simulated execution — realized and unrealized/);
  assert.equal(count(html, /data-pl-trade=/g), 2, "one row per trade");
  assert.match(t, /\$1,012\.50 proceeds/);
  assert.match(t, /\$200\.00 cost/);
  assert.match(t, /-\$40\.00/);
  assert.match(t, /\$212\.50/);
  assert.match(t, /net \$172\.50/);
  assert.match(t, /Account unrealized P\/L at the last platform price SIMULATED : \$88\.00/);
  // one tag in the heading + one per trade's quantity + the totals + the unrealized line
  assert.equal(count(html, /data-testid="pl-simulated"/g), 1 + PL.trades.length + 2, "every figure group labelled");
  assert.equal(count(html, /<button/g), 0, "read-only: no control");
});

test("proposal P/L: a sell the tax engine has not valued shows the server's reason, never a guessed gain", () => {
  const pl = { ...PL, trades: [{ ...PL.trades[0], realized_st: null, realized_lt: null,
    realized_note: "awaiting the custodian's closed lot (the fill names no lots)" }] };
  const t = text(render(h(P.SimulatedFixPL, { envelope: proposalEnv({ fix_sim_pl: pl }) })));
  assert.match(t, /awaiting the custodian's closed lot/);
});

test("proposal P/L fails closed: no fix_sim_pl from the server → nothing rendered", () => {
  assert.equal(render(h(P.SimulatedFixPL, { envelope: proposalEnv() })), "");
  assert.equal(render(h(P.SimulatedFixPL, { envelope: proposalEnv({ fix_sim_pl: { trades: "x" } }) })), "");
  assert.equal(render(h(P.SimulatedFixPL, { envelope: null })), "");
});

test("proposal page: a fix_sim instruction is marked 'automatic — simulated counterparty'", () => {
  const html = render(h(P.default, { initialEnvelope: proposalEnv({ fix_sim_pl: PL }) }));
  assert.equal(count(html, /data-testid="fix-sim-route"/g), 1);
  assert.match(text(html), /automatic — simulated counterparty \(SIMULATED\)/);
  assert.equal(count(html, /data-testid="fix-sim-pl"/g), 1);
});

const SENT = "8=FIX.4.4|9=120|35=D|49=HOLLISWORKS|56=HARBOR_SIM|34=1|52=20261111-15:00:00.000|11=i1|1=X-1|55=FXC|54=1|38=10|40=1|59=0|10=101|";
const GOT = "8=FIX.4.4|9=150|35=8|49=HARBOR_SIM|56=HOLLISWORKS|34=1|52=20261111-15:00:00.010|37=sim-o-1|11=i1|17=sim-e-1|150=F|39=2|31=101.25|32=10|10=044|";
const fields = (raw) => raw.split("|").filter(Boolean).map((kv) => {
  const [tag, value] = kv.split("=");
  return { tag: Number(tag), name: { 35: "MsgType", 11: "ClOrdID", 37: "OrderID", 17: "ExecID", 31: "LastPx" }[tag] || "", value };
});
const insEnv = (fx) => ({
  instruction: { id: "i1", status: "reconciled", side: "buy", quantity: "10", notional: null, price_kind: "market",
    executor_type: "custodian_desk", execute_by: "2026-11-20", security: { name: "Fixture Co" },
    account: { account_number_masked: "*****0001" }, maker_ids: [], filled_quantity: "10", lots: [], fills: [],
    timeline: [], tasks: [], custodian: null, fix_exchange: fx },
  permissions: { can_read: true, can_write: true }, actions: [], vocabularies: { statuses: [], sides: [] },
});

test("instruction FIX exchange: messages sent / received, readable, SIMULATED, 'automatic — simulated counterparty'", () => {
  const fx = { label: "SIMULATED", automatic: true, automatic_label: "automatic — simulated counterparty",
    messages: [{ event_id: "e1", action: "mark_sent", direction: "out", msg_type: "D", msg_name: "NewOrderSingle",
      raw: SENT, fields: fields(SENT) },
    { event_id: "e1", action: "mark_sent", direction: "in", msg_type: "8", msg_name: "ExecutionReport", raw: GOT,
      fields: fields(GOT) }] };
  const html = render(h(D.FixExchangePanel, { envelope: insEnv(fx) }));
  const t = text(html);
  assert.match(t, /FIX exchange/);
  assert.match(t, /SIMULATED/);
  assert.match(t, /automatic — simulated counterparty/);
  assert.match(t, /Sent — NewOrderSingle/);
  assert.match(t, /Received — ExecutionReport/);
  assert.match(t, /37 OrderID: sim-o-1/);
  assert.match(t, /31 LastPx: 101\.25/);
  assert.equal(count(html, /data-fix-direction="out"/g), 1);
  assert.equal(count(html, /data-fix-direction="in"/g), 1);
  assert.equal(count(html, /<button/g), 0, "read-only");
});

test("instruction FIX exchange fails closed: no fix_exchange → nothing; empty → 'No FIX message yet'", () => {
  assert.equal(render(h(D.FixExchangePanel, { envelope: insEnv(null) })), "");
  const t = text(render(h(D.FixExchangePanel, { envelope: insEnv({ messages: [], automatic: false }) })));
  assert.match(t, /No FIX message yet/);
  assert.doesNotMatch(t, /automatic — simulated counterparty/);
});

const LOG = {
  rows: [{ id: "m2", exchange_id: "x1", seq: 1, direction: "out", msg_type: "8", msg_name: "ExecutionReport",
    cl_ord_id: "i1", order_id: "sim-o-1", exec_id: "sim-e-1", valid: true, reject_reason: null, raw: GOT,
    fields: fields(GOT), created_at: "2026-11-11T15:00:00Z" },
  { id: "m1", exchange_id: "x1", seq: 0, direction: "in", msg_type: "D", msg_name: "NewOrderSingle", cl_ord_id: "i1",
    order_id: null, exec_id: null, valid: true, reject_reason: null, raw: SENT, fields: fields(SENT),
    created_at: "2026-11-11T15:00:00Z" },
  { id: "m0", exchange_id: "x0", seq: 0, direction: "in", msg_type: "D", msg_name: "NewOrderSingle", cl_ord_id: null,
    order_id: null, exec_id: null, valid: false, reject_reason: "CheckSum (10) is 000, the message sums to 101",
    raw: "8=FIX.4.4|10=000|", fields: [], created_at: "2026-11-11T14:59:00Z" }],
  notes: ["SIMULATED — FIX 4.4 tag=value carried over HTTP: one message in, the reply message(s) out."],
};

test("Admin → Counterparties → FIX: the message log — received / sent, valid or the Reject reason, readable fields", () => {
  const html = render(h(C.FixMessages, { id: "c1", initialMessages: LOG }));
  const t = text(html);
  assert.equal(count(html, /data-fix-message=/g), 3);
  assert.match(t, /FIX messages SIMULATED/);
  assert.match(t, /Received NewOrderSingle/);
  assert.match(t, /Sent ExecutionReport/);
  assert.match(t, /No — CheckSum \(10\) is 000/);
  assert.match(t, /tag=value carried over HTTP/);
  assert.equal(count(html, /<button/g), 0, "read-only");
});

test("Admin FIX log: empty → 'No FIX messages yet'", () => {
  const t = text(render(h(C.FixMessages, { id: "c1", initialMessages: { rows: [], notes: [] } })));
  assert.match(t, /No FIX messages yet/);
});
