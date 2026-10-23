// REP2 — the REAL execution / capabilities / my-tasks components rendered with
// react-dom/server from envelopes shaped exactly like routers/execution.py,
// routers/custodians.py and routers/human_tasks.py return them. Every write or
// approve control (New, Edit, Submit, Mark sent, Enter fill, Reconcile, Close,
// Cancel, Approve, Reject, capability Edit) must FAIL CLOSED: none without the
// server's own flag being exactly true AND, on the detail page, the action in
// the server's `actions` list; Approve / Reject also never for a maker.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { register } from "node:module";
import test from "node:test";

register("../market/jsxLoader.mjs", import.meta.url);

const { createElement: h } = await import("react");
const { renderToStaticMarkup } = await import("react-dom/server");
const ex = await import("../../lib/execution.mjs");
const { default: InstructionList } = await import("../../components/execution/InstructionList.jsx");
const { default: InstructionDetail } = await import("../../components/execution/InstructionDetail.jsx");
const { default: CapabilityMatrix } = await import("../../components/custodians/CapabilityMatrix.jsx");
const { MyTasksView } = await import("../../components/dashboard/MyTasks.jsx");
const menu = await import("../../lib/menuVisibility.mjs");

const render = (el) => renderToStaticMarkup(el);
const text = (html) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&")
  .replace(/&quot;/g, '"').replace(/\s+/g, " ");
const buttons = (html) => (html.match(/<button[^>]*>[^<]*<\/button>/g) || []).map((b) => text(b).trim());

const VOCAB = {
  statuses: [{ key: "draft", label: "Draft" }, { key: "pending_approval", label: "Awaiting approval" },
    { key: "approved", label: "Approved" }, { key: "sent", label: "Sent" }, { key: "filled", label: "Filled" }],
  executor_types: [{ key: "member", label: "Member" }, { key: "broker", label: "Broker" }],
  sides: [{ key: "buy", label: "Buy" }, { key: "sell", label: "Sell" }],
  price_kinds: [{ key: "market", label: "At market" }, { key: "limit", label: "Limit" }],
  if_not_filled: [{ key: "ask", label: "Ask me" }],
  sent_channels: [{ key: "email", label: "Email" }],
  reconcile_methods: [{ key: "manual", label: "Confirmed manually" }],
  actions: [{ key: "create", label: "Created" }],
  editable: [],
};
const PERMS_VIEW = { can_read: true, can_write: false, can_approve: false, is_super_admin: false,
  read_permission: "view_execution", write_permission: "manage_execution", approve_permission: "approve_execution" };
const ROW = { id: "i1", status: "draft", side: "sell", quantity: "10", notional: null, price_kind: "limit",
  limit_price: "101.25", executor_type: "member", executor_name: "Pat", execute_by: "2026-10-30",
  security: { name: "Test Co", ticker: "TST" }, account: { account_number_masked: "*****6789", is_held_away: true } };
const ALL_ACTIONS = ["edit", "submit", "cancel", "mark_sent", "fill", "reconcile", "close", "approve", "reject",
  "letter"];
const detailEnvelope = (permissions, actions, extra = {}) => ({
  instruction: { ...ROW, maker_ids: ["u1"], filled_quantity: "0", lots: [], fills: [], timeline: [],
    tasks: [{ id: "t1", status: "open", eligible_permission: "approve_execution", maker_id: "u1", maker_name: "Maker" }],
    ...extra },
  permissions, actions, vocabularies: VOCAB,
});
const WRITE_BUTTONS = ["New instruction", "Submit for approval", "Mark sent", "Enter fill", "Reconcile", "Close",
  "Cancel instruction", "Approve", "Reject", "Save draft", "Edit", "Save new version"];

test("instructions list: no New control unless the envelope says can_write === true", () => {
  for (const env of [
    { rows: [ROW], permissions: PERMS_VIEW, vocabularies: VOCAB },
    { rows: [ROW], vocabularies: VOCAB },                                         // envelope lost its permissions
    { rows: [ROW], permissions: { ...PERMS_VIEW, can_write: "true" }, vocabularies: VOCAB },   // not exactly true
    null,
  ]) {
    const html = render(h(InstructionList, { initialEnvelope: env, accounts: [] }));
    assert.ok(!buttons(html).includes("New instruction"), `no New control for ${JSON.stringify(env?.permissions)}`);
  }
  const html = render(h(InstructionList, { initialEnvelope: { rows: [ROW], permissions: { ...PERMS_VIEW,
    can_write: true }, vocabularies: VOCAB }, accounts: [] }));
  assert.ok(buttons(html).includes("New instruction"));
  assert.match(text(html), /Sell 10 sh Test Co \(TST\)/);
  assert.match(text(html), /\*\*\*\*\*6789 held away/);
});

test("instruction detail: a view-only envelope renders no write or approve control (only the letter)", () => {
  const html = render(h(InstructionDetail, { initialEnvelope: detailEnvelope(PERMS_VIEW, ALL_ACTIONS) }));
  const b = buttons(html);
  for (const w of WRITE_BUTTONS) assert.ok(!b.includes(w), `no ${w} for a view-only caller: ${b}`);
  assert.ok(b.includes("Download letter"), "viewing includes the on-demand letter");
  for (const env of [detailEnvelope({}, ALL_ACTIONS), detailEnvelope(undefined, ALL_ACTIONS), { instruction: ROW }]) {
    const bb = buttons(render(h(InstructionDetail, { initialEnvelope: env })));
    for (const w of [...WRITE_BUTTONS, "Download letter"]) assert.ok(!bb.includes(w), `fail closed: no ${w}`);
  }
});

test("instruction detail: can_write alone shows nothing the server's actions list does not name", () => {
  const writer = { ...PERMS_VIEW, can_write: true };
  const none = buttons(render(h(InstructionDetail, { initialEnvelope: detailEnvelope(writer, []) })));
  for (const w of WRITE_BUTTONS) assert.ok(!none.includes(w), `no ${w} without the action`);
  const noVocab = buttons(render(h(InstructionDetail, { initialEnvelope: detailEnvelope(writer,
    ["edit", "submit", "cancel"]) })));
  assert.ok(!noVocab.includes("Save draft"), "no edit form while the server's editable vocabulary is empty");
  const draft = buttons(render(h(InstructionDetail, { initialEnvelope: { ...detailEnvelope(writer,
    ["edit", "submit", "cancel"]), vocabularies: { ...VOCAB, editable: ["quantity", "execute_by"] } } })));
  assert.ok(draft.includes("Submit for approval") && draft.includes("Save draft") && draft.includes("Cancel instruction"));
  assert.ok(!draft.includes("Approve") && !draft.includes("Enter fill"));
  const sent = buttons(render(h(InstructionDetail, { initialEnvelope: detailEnvelope(writer, ["fill", "cancel"],
    { status: "sent" }) })));
  assert.ok(sent.includes("Enter fill") && !sent.includes("Submit for approval"));
});

test("approve / reject: only with approve_execution, can_decide, and never for a maker", () => {
  const actions = ["approve", "reject"];
  const approver = { ...PERMS_VIEW, can_approve: true, can_decide: true, is_maker: false };
  const yes = buttons(render(h(InstructionDetail, { initialEnvelope: detailEnvelope(approver, actions,
    { status: "pending_approval" }) })));
  assert.ok(yes.includes("Approve") && yes.includes("Reject"));
  for (const p of [{ ...approver, is_maker: true }, { ...approver, can_decide: false },
    { ...approver, can_approve: false }, { ...approver, can_decide: "true" }]) {
    const b = buttons(render(h(InstructionDetail, { initialEnvelope: detailEnvelope(p, actions,
      { status: "pending_approval" }) })));
    assert.ok(!b.includes("Approve") && !b.includes("Reject"), `no decision for ${JSON.stringify(p)}`);
  }
});

const CAP_VOCAB = {
  capabilities: [{ key: "equity_trading_api", label: "Equity trading API", values: ["yes", "no", "unknown"] }],
  custodians: [{ key: "altruist", label: "Altruist" }],
  values: [{ key: "yes", label: "Yes" }, { key: "no", label: "No" }, { key: "unknown", label: "Unknown" }],
  evidence_kinds: [{ key: "document", label: "Document" }, { key: "assumed", label: "Assumed" }],
  unknown_rule: "Unknown is treated as not available.", editable: [],
};
const CAP_ROWS = [{ custodian_code: "altruist", label: "Altruist", cells: [{ custodian_code: "altruist",
  capability_key: "equity_trading_api", value: "unknown", available: false, evidence_kind: "document",
  evidence: "scope accounts:read", as_of: "2026-10-22", version_no: 1, recorded: true }] }];

test("capabilities: Edit only for Super Admin; 'unknown' shown plainly", () => {
  for (const p of [{ can_read: true, can_write: false, is_super_admin: false }, {}, { can_read: true,
    can_write: true, is_super_admin: false }, { can_read: true, can_write: "true", is_super_admin: true }]) {
    const html = render(h(CapabilityMatrix, { initialEnvelope: { rows: CAP_ROWS, permissions: p, vocabularies: CAP_VOCAB } }));
    assert.ok(!buttons(html).includes("Edit"), `no Edit for ${JSON.stringify(p)}`);
    assert.match(text(html), /Unknown/);
    assert.match(text(html), /Unknown is treated as not available/);
  }
  const html = render(h(CapabilityMatrix, { initialEnvelope: { rows: CAP_ROWS, permissions: { can_read: true,
    can_write: true, is_super_admin: true }, vocabularies: CAP_VOCAB } }));
  assert.ok(buttons(html).includes("Edit"));
});

test("menu: Instructions gated on view_execution (strict); Custodian capabilities on staff (strict)", () => {
  const ins = menu.MENU_ITEMS.find((i) => i.href === "/portfolio/instructions");
  const cap = menu.MENU_ITEMS.find((i) => i.href === "/admin/custodian-capabilities");
  assert.equal(ins.group, "portfolio");
  assert.equal(cap.group, "billing");
  const me = (extra) => ({ account_role: "member", status: "active", roles: ["x"], permissions: [], ...extra });
  assert.equal(menu.canAccess(me({ permissions: ["view_execution"] }), ins.gate), true);
  assert.equal(menu.canAccess(me({}), ins.gate), false);
  assert.equal(menu.canAccess({ account_role: "member" }, ins.gate), false);
  assert.equal(menu.canAccess(me({ account_role: "super_admin" }), ins.gate), true);
  assert.equal(menu.canAccess(me({ is_staff: true }), cap.gate), true);
  assert.equal(menu.canAccess(me({ is_staff: "true" }), cap.gate), false);
  assert.equal(menu.canAccess(me({}), cap.gate), false);
  assert.equal(menu.canAccess(me({ is_staff: true, status: "pending" }), cap.gate), false);
  assert.equal(menu.canAccess(me({ account_role: "super_admin" }), cap.gate), true);
});

test("trade-list body: quantities and prices go as decimal strings, never numbers", () => {
  const body = ex.tradeListBody({ accountId: "a", securityId: "s", side: "sell", amountMode: "quantity",
    amount: "1,000.5", priceKind: "limit", limitPrice: "12.30", executorType: "member", executeBy: "2026-10-30",
    lots: [{ id: "l1", quantity: "400" }, { id: "l2", quantity: "" }] });
  assert.equal(body.trades[0].quantity, "1000.5");
  assert.equal(body.trades[0].price_guidance.limit_price, "12.30");
  assert.deepEqual(body.trades[0].lot_instructions, [{ tax_lot_id: "l1", quantity: "400" }]);
  assert.equal(typeof body.trades[0].quantity, "string");
  assert.equal(ex.decimalString("abc"), null);
  assert.equal(ex.decimalString(""), null);
});

test("my tasks: lists open tasks the server returned; nothing when there are none", () => {
  assert.equal(render(h(MyTasksView, { payload: null })), "");
  assert.equal(render(h(MyTasksView, { payload: { actionable: [], waiting_on_others: [] } })), "");
  const html = render(h(MyTasksView, { payload: {
    actionable: [{ id: "t1", title: "Approve instruction: Sell 10 shares of Test Co", kind: "execution_approval",
      action_path: "/portfolio/instructions/i1", opened_at: "2026-10-22T10:00:00" }],
    waiting_on_others: [], vocabularies: { kinds: [{ key: "execution_approval", label: "Execution approval" }] } } }));
  assert.match(text(html), /Approve instruction: Sell 10 shares of Test Co/);
  assert.match(text(html), /Execution approval/);
  assert.match(html, /href="\/portfolio\/instructions\/i1"/);
  assert.equal(buttons(html).length, 0, "the panel itself decides nothing");
});

// verify_rep2.py writes the REAL envelopes it received from the API (a
// view-only caller, the maker and the checker of a pending instruction, an org
// admin and Super Admin on the capabilities matrix) to a JSON file and points
// REP2_REAL_ENVELOPES at it, so the client-side proof runs on what the server
// actually sent — not on hand-made fixtures. Skipped when run on its own.
const REAL = process.env.REP2_REAL_ENVELOPES;
test("real API envelopes: no write / approve controls without the server's flags", { skip: !REAL }, () => {
  const env = JSON.parse(readFileSync(REAL, "utf8"));
  const listView = buttons(render(h(InstructionList, { initialEnvelope: env.viewer_list, accounts: [] })));
  assert.ok(!listView.includes("New instruction"), "viewer list: no New");
  const listAdmin = buttons(render(h(InstructionList, { initialEnvelope: env.admin_list, accounts: [] })));
  assert.ok(listAdmin.includes("New instruction"), "admin list: New");
  const viewer = buttons(render(h(InstructionDetail, { initialEnvelope: env.viewer_detail })));
  for (const w of WRITE_BUTTONS) assert.ok(!viewer.includes(w), `viewer detail: no ${w} (${viewer})`);
  const maker = buttons(render(h(InstructionDetail, { initialEnvelope: env.maker_pending })));
  assert.ok(!maker.includes("Approve") && !maker.includes("Reject"), `maker: no decision (${maker})`);
  const checker = buttons(render(h(InstructionDetail, { initialEnvelope: env.checker_pending })));
  assert.ok(checker.includes("Approve") && checker.includes("Reject"), `checker: Approve + Reject (${checker})`);
  const writerDraft = buttons(render(h(InstructionDetail, { initialEnvelope: env.writer_draft })));
  assert.ok(writerDraft.includes("Submit for approval") && writerDraft.includes("Save draft"), `writer draft (${writerDraft})`);
  assert.ok(Array.isArray(env.staff_capabilities?.rows) && env.staff_capabilities.rows.length > 0,
    "the staff envelope is a real matrix, not an error");
  const capStaff = buttons(render(h(CapabilityMatrix, { initialEnvelope: env.staff_capabilities })));
  assert.ok(!capStaff.includes("Edit"), "staff: capabilities read-only");
  const capSuper = buttons(render(h(CapabilityMatrix, { initialEnvelope: env.super_capabilities })));
  assert.ok(capSuper.includes("Edit"), "Super Admin: Edit");
});
