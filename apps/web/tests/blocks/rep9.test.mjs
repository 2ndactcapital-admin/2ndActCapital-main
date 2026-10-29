// REP9 — the REAL block-trading components rendered with react-dom/server from
// envelopes shaped exactly like routers/blocks.py returns them. Every write /
// approve control (New block, Build, Submit, Mark sent, Enter fill, Allocate,
// Record settlement, Ask for correction, Approve / Reject, Approve correction,
// Add charge, Close, Cancel, policy Submit / Approve, executing-account Add /
// Retire) must FAIL CLOSED: none without the server's own flag being exactly
// true AND, on a block, the action in the server's `actions` list; Approve /
// Reject never for a maker. The order execution sheet is offered to a reader
// (manual is always available), including on a single broker instruction.
import assert from "node:assert/strict";
import { register } from "node:module";
import test from "node:test";

register("../market/jsxLoader.mjs", import.meta.url);

const { createElement: h } = await import("react");
const { renderToStaticMarkup } = await import("react-dom/server");
const bl = await import("../../lib/blocks.mjs");
const { default: BlockList } = await import("../../components/blocks/BlockList.jsx");
const { default: BlockDetail } = await import("../../components/blocks/BlockDetail.jsx");
const { default: AllocationPolicy } = await import("../../components/blocks/AllocationPolicy.jsx");
const { default: ExecutionAccounts } = await import("../../components/blocks/ExecutionAccounts.jsx");
const { default: InstructionDetail } = await import("../../components/execution/InstructionDetail.jsx");
const menu = await import("../../lib/menuVisibility.mjs");

const render = (el) => renderToStaticMarkup(el);
const text = (html) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&")
  .replace(/&quot;/g, '"').replace(/\s+/g, " ");
const buttons = (html) => (html.match(/<button[^>]*>[^<]*<\/button>/g) || []).map((b) => text(b).trim());

const VOCAB = {
  statuses: [{ key: "draft", label: "Draft" }, { key: "pending_approval", label: "Awaiting approval" },
    { key: "allocated", label: "Allocated" }],
  allocation_statuses: [{ key: "allocated", label: "Allocated" }, { key: "fixed", label: "Fixed at approval" }],
  settlement_statuses: [{ key: "pending", label: "Pending" }, { key: "affirmed", label: "Affirmed" },
    { key: "settled", label: "Settled" }, { key: "failed", label: "Failed" }],
  settlement_kinds: [{ key: "dvp", label: "Another custodian — DVP" }, { key: "integrated", label: "Integrated" }],
  executor_types: [{ key: "broker", label: "Broker" }],
  sides: [{ key: "buy", label: "Buy" }, { key: "sell", label: "Sell" }],
  price_kinds: [{ key: "market", label: "At market" }, { key: "limit", label: "Limit" }],
  if_not_filled: [{ key: "ask", label: "Ask me" }],
  sent_channels: [{ key: "email", label: "Email" }],
  routes: [{ key: "manual", label: "Manual — print an order execution sheet" }, { key: "fix", label: "FIX router — not connected" }],
  reference_sources: [{ key: "security_data", label: "Security-data layer" }, { key: "adviser", label: "Typed by the adviser" }],
  methods: [{ key: "pro_rata_requested", label: "Pro rata on the quantity requested" }],
  rounding_rules: [{ key: "largest_remainder_then_account", label: "Largest remainder" }],
  partial_fill_rules: [{ key: "pro_rata_allocated", label: "Pro rata" }],
  policy_statuses: [{ key: "draft", label: "Awaiting approval" }, { key: "approved", label: "Approved" }],
  correction_statuses: [{ key: "draft", label: "Awaiting approval" }],
  charge_kinds: [{ key: "commission", label: "Commission" }],
  execution_account_statuses: [{ key: "active", label: "Active" }],
  actions: [{ key: "create", label: "Built" }],
  compliance_notes: ["Best-execution review of each executing broker is the firm's."],
  editable: [],
};
const VIEW = { can_read: true, can_write: false, can_approve: false, is_super_admin: false,
  read_permission: "view_blocks", write_permission: "manage_blocks", approve_permission: "approve_blocks" };
const ALL = ["submit", "cancel", "mark_sent", "fill", "allocate", "settlement", "correct", "close", "charge", "approve",
  "reject", "decide_correction", "sheet"];
const BLOCK = {
  id: "b1", status: "allocated", side: "buy", total_quantity: "300", filled_quantity: "250", average_price: "10.017240",
  executor_type: "broker", executor_name: "ZZ Broker", route: "manual", price_kind: "limit", limit_price: "10.50",
  reference: { price: "10.000000", source: "security_data", provider: "custodian_valuation", as_of: "2026-10-28" },
  execute_by: "2026-10-30", if_not_filled: "ask", maker_ids: ["u1"], security: { name: "Test Co", ticker: "TST" },
  execution_account: { broker_name: "ZZ Broker", account_reference_masked: "****9876" },
  policy: { version_no: 1, minimum_allocation: "5" },
  allocations: [
    { id: "a1", account_number_masked: "*****0001", requested_quantity: "200", allocated_quantity: "200",
      final_quantity: "167", status: "allocated", settlement_kind: "dvp", settlement_status: "pending",
      receiving_custodian: "ZZ Receiving Trust",
      settlement: { receiving_custodian_name: "ZZ Receiving Trust", receiving_participant_number: "0901",
        receiving_account_masked: "****4321", settlement_date: "2026-10-30" } },
    { id: "a2", account_number_masked: "*****0002", requested_quantity: "100", allocated_quantity: "100",
      final_quantity: "83", status: "allocated", settlement_kind: "integrated", settlement_status: "pending",
      receiving_custodian: "ALTRUIST" },
  ],
  fills: [], timeline: [], charges: [],
  corrections: [{ id: "c1", status: "draft", reason: "trade error", maker_id: "u1",
    changes: [{ allocation_id: "a1", account: "*****0001", before: "167", after: "166" }] }],
  tasks: [{ id: "t1", status: "open", eligible_permission: "approve_blocks" }],
};
const env = (permissions, actions, extra = {}) => ({ block: { ...BLOCK, ...extra }, permissions, actions,
  vocabularies: VOCAB });
const WRITE = ["New block", "Build block", "Submit for approval", "Mark sent", "Enter fill", "Allocate at the average price",
  "Record settlement", "Ask for correction", "Approve", "Reject", "Approve correction", "Reject correction", "Add charge",
  "Close block", "Cancel block", "Approve version", "Reject version", "Add executing account", "Retire"];

test("blocks list: no New block without can_write === true and the server's build action", () => {
  for (const e of [
    { rows: [], permissions: VIEW, vocabularies: VOCAB, actions: [] },
    { rows: [], vocabularies: VOCAB, actions: ["build"] },                                  // lost permissions
    { rows: [], permissions: { ...VIEW, can_write: "true" }, vocabularies: VOCAB, actions: ["build"] },
    { rows: [], permissions: { ...VIEW, can_write: true }, vocabularies: VOCAB, actions: [] },  // flag but no action
    null,
  ]) {
    const b = buttons(render(h(BlockList, { initialEnvelope: e, candidates: null })));
    assert.ok(!b.includes("New block"), `no New block for ${JSON.stringify(e?.permissions)} ${JSON.stringify(e?.actions)}`);
  }
  const ok = { rows: [], permissions: { ...VIEW, can_write: true }, vocabularies: VOCAB, actions: ["build"] };
  assert.ok(buttons(render(h(BlockList, { initialEnvelope: ok, candidates: null }))).includes("New block"));
  assert.match(text(render(h(BlockList, { initialEnvelope: ok }))), /Best-execution review/, "compliance notes shown");
});

test("block detail: a view-only envelope renders no write / approve / allocate / correct control (sheet only)", () => {
  const html = render(h(BlockDetail, { initialEnvelope: env(VIEW, ALL) }));
  const b = buttons(html);
  for (const w of WRITE) assert.ok(!b.includes(w), `no ${w} for a view-only caller: ${b}`);
  assert.ok(b.includes("Download order execution sheet"), "the manual route's sheet is offered to a reader");
  for (const e of [env({}, ALL), env(undefined, ALL), { block: BLOCK }, env({ ...VIEW, can_read: "true" }, ALL)]) {
    const bb = buttons(render(h(BlockDetail, { initialEnvelope: e })));
    for (const w of [...WRITE, "Download order execution sheet"]) assert.ok(!bb.includes(w), `fails closed: ${w} ${bb}`);
  }
});

test("block detail: write flags without the server's actions render nothing; with them, exactly those", () => {
  const p = { ...VIEW, can_write: true, can_approve: true, can_decide: true, is_maker: false };
  const none = buttons(render(h(BlockDetail, { initialEnvelope: env(p, []) })));
  for (const w of WRITE) assert.ok(!none.includes(w), `no ${w} without the action: ${none}`);
  const b = buttons(render(h(BlockDetail, { initialEnvelope: env(p, ["settlement", "correct", "decide_correction", "sheet"]) })));
  for (const w of ["Record settlement", "Ask for correction", "Approve correction", "Download order execution sheet"]) {
    assert.ok(b.includes(w), `${w} shown when listed: ${b}`);
  }
  for (const w of ["Allocate at the average price", "Approve", "Mark sent"]) assert.ok(!b.includes(w), `${w} not listed`);
});

test("approve / reject: never for a maker, never without can_decide === true, never without approve_blocks", () => {
  const base = { ...VIEW, can_write: true, can_approve: true, can_decide: true, is_maker: false };
  const acts = ["approve", "reject"];
  const pending = { status: "pending_approval" };
  assert.ok(buttons(render(h(BlockDetail, { initialEnvelope: env(base, acts, pending) }))).includes("Approve"));
  for (const p of [{ ...base, is_maker: true }, { ...base, can_decide: false }, { ...base, can_approve: false },
    { ...base, can_decide: "true" }]) {
    const b = buttons(render(h(BlockDetail, { initialEnvelope: env(p, acts, pending) })));
    assert.ok(!b.includes("Approve") && !b.includes("Reject"), `no decision for ${JSON.stringify(p)}`);
  }
  // correction decisions: approve_blocks AND the server's decide_correction
  const noApprove = buttons(render(h(BlockDetail, { initialEnvelope: env({ ...base, can_approve: false },
    ["decide_correction"]) })));
  assert.ok(!noApprove.includes("Approve correction"));
});

test("allocation table: masked accounts, DVP details and the integrated receive; price guidance with source + as-of", () => {
  const t = text(render(h(BlockDetail, { initialEnvelope: env(VIEW, ["sheet"]) })));
  assert.match(t, /\*\*\*\*\*0001/);
  assert.match(t, /DVP to ZZ Receiving Trust · participant 0901 · account \*\*\*\*4321 · settles 2026-10-30/);
  assert.match(t, /Receive versus payment at ALTRUIST \(integrated custodian\)/);
  assert.match(t, /reference 10\.000000 as of 2026-10-28 \(Security-data layer: custodian_valuation\)/);
  assert.equal(bl.guidanceText({ price_kind: "market", reference: {} }, VOCAB), "At market · no reference price on record");
});

test("allocation policy: Submit / Approve only from the envelope's flags and per-version actions", () => {
  const row = { id: "p1", version_no: 1, status: "draft", method: "pro_rata_requested", minimum_allocation: "5",
    rounding_rule: "largest_remainder_then_account", partial_fill_rule: "pro_rata_allocated", policy_text: "Our policy." };
  for (const e of [{ rows: [row], permissions: VIEW, actions: { p1: ["approve", "reject"] }, vocabularies: VOCAB },
    { rows: [row], permissions: { ...VIEW, can_approve: true }, actions: { p1: [] }, vocabularies: VOCAB },
    { rows: [row], actions: { p1: ["approve"] }, can_request: true, vocabularies: VOCAB }]) {
    const b = buttons(render(h(AllocationPolicy, { initialEnvelope: e })));
    for (const w of ["Approve version", "Reject version", "Submit for approval"]) assert.ok(!b.includes(w), `${w}: ${b}`);
  }
  const ok = { rows: [row], permissions: { ...VIEW, can_write: true, can_approve: true }, actions: { p1: ["approve", "reject"] },
    can_request: true, vocabularies: VOCAB };
  const b = buttons(render(h(AllocationPolicy, { initialEnvelope: ok })));
  for (const w of ["Approve version", "Reject version", "Submit for approval"]) assert.ok(b.includes(w), `${w}: ${b}`);
});

test("executing accounts: Add / Retire only with can_write === true", () => {
  const rows = [{ id: "e1", broker_name: "ZZ Broker", account_reference_masked: "****9876", dvp_capable: true, status: "active" }];
  for (const p of [VIEW, undefined, { ...VIEW, can_write: "true" }]) {
    const b = buttons(render(h(ExecutionAccounts, { initialEnvelope: { rows, permissions: p, vocabularies: VOCAB } })));
    assert.ok(!b.includes("Add executing account") && !b.includes("Retire"), `${JSON.stringify(p)} ${b}`);
  }
  const b = buttons(render(h(ExecutionAccounts, { initialEnvelope: { rows, permissions: { ...VIEW, can_write: true },
    vocabularies: VOCAB } })));
  assert.ok(b.includes("Add executing account") && b.includes("Retire"));
  assert.match(text(render(h(ExecutionAccounts, { initialEnvelope: { rows, permissions: VIEW, vocabularies: VOCAB } }))), /\*\*\*\*9876/);
});

test("a single broker instruction offers Print order execution sheet (manual always available); a member's does not", () => {
  const perms = { can_read: true, can_write: false, can_approve: false };
  const ins = { id: "i1", status: "approved", side: "buy", quantity: "10", executor_type: "broker", maker_ids: ["u1"],
    lots: [], fills: [], timeline: [], tasks: [], security: { name: "Test Co" }, account: { account_number_masked: "*****0001" } };
  const vocab = { statuses: [], executor_types: [], sides: [], price_kinds: [], if_not_filled: [], actions: [], editable: [] };
  const withSheet = buttons(render(h(InstructionDetail, { initialEnvelope: { instruction: ins, permissions: perms,
    actions: ["order_sheet"], vocabularies: vocab } })));
  assert.ok(withSheet.includes("Print order execution sheet"), `${withSheet}`);
  const without = buttons(render(h(InstructionDetail, { initialEnvelope: { instruction: { ...ins, executor_type: "member" },
    permissions: perms, actions: ["letter"], vocabularies: vocab } })));
  assert.ok(!without.includes("Print order execution sheet"));
  const lost = buttons(render(h(InstructionDetail, { initialEnvelope: { instruction: ins, actions: ["order_sheet"],
    vocabularies: vocab } })));
  assert.ok(!lost.includes("Print order execution sheet"), "fails closed on a lost envelope");
});

test("menu: Blocks / Allocation policy / Executing accounts are STRICT view_blocks gates", () => {
  for (const href of ["/portfolio/blocks", "/admin/allocation-policy", "/admin/executing-accounts"]) {
    const item = menu.MENU_ITEMS.find((i) => i.href === href);
    assert.ok(item, href);
    assert.deepEqual(item.gate, { perm: "view_blocks", strict: true });
  }
});

test("buildBody: decimals as strings, route always manual, floats never sent", () => {
  const b = bl.buildBody({ instructionIds: ["i1", "i2"], priceKind: "limit", limitPrice: "10.50", referencePrice: "abc" });
  assert.deepEqual(b.instruction_ids, ["i1", "i2"]);
  assert.equal(b.route, "manual");
  assert.equal(b.limit_price, "10.50");
  assert.equal(b.reference_price, undefined);
});
