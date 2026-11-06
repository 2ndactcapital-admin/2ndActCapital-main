// BULKAPPROVE1 — Approvals → Bulk approve: the REAL lib and components rendered in Node from envelopes written by
// hand in the API's shape (routers/approvals.py + services/bulk_approvals.py), never imported from it. Proves the
// client side of every write-gated control: a view-only envelope renders NO decision control; greyed rows carry the
// server's reason; select all acts on the filtered set and never takes a blocked item; one confirmation names the
// count and lists the items; reject needs a note; the entry-point link shows only for more than one decidable item.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { register } from "node:module";
import test from "node:test";

register("../market/jsxLoader.mjs", import.meta.url);

const { createElement: h } = await import("react");
const { renderToStaticMarkup } = await import("react-dom/server");
const B = await import("../../lib/bulkApprove.mjs");
const { BulkApproveView, ConfirmView } = await import("../../components/approvals/BulkApprove.jsx");
const { BulkPendingLinkView } = await import("../../components/approvals/BulkPendingLink.jsx");
const { MyTasksView } = await import("../../components/dashboard/MyTasks.jsx");
const menu = await import("../../lib/menuVisibility.mjs");

const src = (p) => readFileSync(new URL(`../../${p}`, import.meta.url), "utf8");
const noop = () => {};

const VOCAB = {
  types: [{ key: "authority", label: "Authority & custody" }, { key: "held_away", label: "Held-away entries" }],
  subtypes: [{ key: "household", label: "Household authority" }, { key: "account", label: "Account authority / custody" }],
  households: [{ key: "h1", label: "Fixture Household One" }, { key: "h2", label: "Fixture Household Two" }],
  makers: [{ key: "m", label: "Maker M" }, { key: "c", label: "Checker C" }],
  ages: [{ key: "any", label: "Any age", min_days: null, max_days: null },
    { key: "today", label: "Today", min_days: null, max_days: 0 },
    { key: "week", label: "Up to 7 days", min_days: null, max_days: 7 },
    { key: "older", label: "Older than 7 days", min_days: 8, max_days: null }],
  decisions: [{ key: "approve", label: "Approve" }, { key: "reject", label: "Reject" }],
  results: [{ key: "approved", label: "Approved" }, { key: "rejected", label: "Rejected" }, { key: "refused", label: "Refused" }],
  blocked: [{ key: "maker", label: "You made this — a different person must decide it" },
    { key: "permission", label: "You do not hold {perm}" }],
};
const row = (id, o = {}) => ({
  id, type: "authority", subtype: "account", subtype_label: "Account authority / custody", household_id: "h1",
  household_name: "Fixture Household One", account_id: `a-${id}`, account_masked: `****00${id}`,
  from_label: "Read-only — data only · Outside the platform", to_label: "Advise — one-off · Another custodian — settled DVP",
  reason: `reason ${id}`, maker_id: "m", maker_name: "Maker M", requested_at: "2026-11-05T10:00:00+00:00", age_days: 0,
  task_status: "open", actions: ["approve", "reject"], blocked: null, blocked_reason: null, ...o,
});
const ROWS = [
  row("1"), row("2"), row("3", { household_id: "h2", household_name: "Fixture Household Two", age_days: 12 }),
  row("4", { maker_id: "c", maker_name: "Checker C", actions: [], blocked: "maker",
    blocked_reason: "You made this — a different person must decide it" }),
  row("5", { task_status: "cancelled", actions: [], blocked: "not_pending",
    blocked_reason: "No longer pending — its approval task is cancelled" }),
  row("6", { subtype: "household", subtype_label: "Household authority", account_id: null, account_masked: null }),
];
const env = (o = {}) => ({
  type: "authority", type_label: "Authority & custody", rows: ROWS, limits: { max_items: 200 },
  permissions: { can_read: true, can_write: true, can_approve: true, is_super_admin: false,
    read_permission: "view_authority", write_permission: "approve_authority", approve_permission: "approve_authority" },
  vocabularies: { ...VOCAB, editable: ["decision"], inline_editable: [] }, ...o,
});
const VIEW_ONLY = env({
  permissions: { can_read: true, can_write: false, can_approve: false, is_super_admin: false,
    read_permission: "view_authority", write_permission: "approve_authority", approve_permission: "approve_authority" },
  vocabularies: { ...VOCAB, editable: [], inline_editable: [] },
  rows: ROWS.map((r) => ({ ...r, actions: [], blocked: "permission", blocked_reason: "You do not hold approve_authority" })),
});
const view = (e, o = {}) => renderToStaticMarkup(h(BulkApproveView, {
  envelope: e, selected: [], filters: { age: "any" }, expanded: null, onToggle: noop, onSelectAll: noop, onClear: noop,
  onFilter: noop, onExpand: noop, onAsk: noop, ...o,
}));

test("an approver sees the pending items, a checkbox only on the decidable ones, greyed rows with the server's reason", () => {
  const html = view(env());
  for (const r of ROWS) assert.match(html, new RegExp(`data-item="${r.id}"`));
  assert.match(html, /Approve selected/);
  assert.match(html, /Reject selected/);
  assert.match(html, /data-item="4" data-selectable="0"/);
  assert.match(html, /data-item="5" data-selectable="0"/);
  assert.match(html, /data-item="1" data-selectable="1"/);
  assert.match(html, /You made this — a different person must decide it/);
  assert.match(html, /No longer pending — its approval task is cancelled/);
  assert.match(html, /Read-only — data only · Outside the platform → Advise/);
  assert.equal((html.match(/type="checkbox"/g) || []).length, ROWS.length);
  assert.equal((html.match(/type="checkbox"[^>]*disabled=""/g) || []).length, 2);
});

test("a view-only envelope renders NO decision control and every row greyed with the permission reason", () => {
  const html = view(VIEW_ONLY);
  assert.doesNotMatch(html, /Approve selected|Reject selected|Select all|type="checkbox"/);
  assert.match(html, /You do not hold approve_authority/);
  assert.equal(B.canDecide(VIEW_ONLY), false);
  assert.deepEqual(B.selectAll(VIEW_ONLY, ROWS), []);
});

test("fails CLOSED: a lost envelope, a truthy-but-not-true flag or a missing editable list grants nothing", () => {
  for (const e of [null, {}, env({ permissions: undefined }), env({ permissions: { can_approve: "true" } }),
    env({ vocabularies: { ...VOCAB } }), env({ vocabularies: { ...VOCAB, editable: [] } })]) {
    assert.equal(B.canDecide(e), false);
    assert.deepEqual(B.selectAll(e, ROWS), []);
    assert.equal(B.bulkControls(e, ["1"]).approve, false);
  }
  assert.equal(B.maxItems(env({ limits: undefined })), 0);
  assert.equal(B.bulkControls(env({ limits: undefined }), ["1"]).approve, false, "no server cap → nothing may be sent");
});

test("filters: household, maker, type, age — each narrows to exactly its predicate; select all = the filtered decidable set", () => {
  const e = env();
  assert.deepEqual(B.filterRows(e, { household: "h2" }).map((r) => r.id), ["3"]);
  assert.deepEqual(B.filterRows(e, { maker: "c" }).map((r) => r.id), ["4"]);
  assert.deepEqual(B.filterRows(e, { subtype: "household" }).map((r) => r.id), ["6"]);
  assert.deepEqual(B.filterRows(e, { age: "older" }).map((r) => r.id), ["3"]);
  assert.deepEqual(B.filterRows(e, { age: "today" }).map((r) => r.id), ["1", "2", "4", "5", "6"]);
  assert.equal(B.filterRows(e, { age: "any" }).length, ROWS.length);
  assert.deepEqual(B.selectAll(e, B.filterRows(e, {})), ["1", "2", "3", "6"]);
  assert.deepEqual(B.selectAll(e, B.filterRows(e, { household: "h1" })), ["1", "2", "6"]);
  assert.deepEqual(B.pruneSelection(["1", "3"], B.filterRows(e, { household: "h1" })), ["1"]);
});

test("toggle never selects a blocked item; the buttons follow the server's per-item actions and the cap", () => {
  const e = env();
  assert.deepEqual(B.toggle(e, [], ROWS[3]), []);
  assert.deepEqual(B.toggle(e, [], ROWS[0]), ["1"]);
  assert.deepEqual(B.toggle(e, ["1"], ROWS[0]), []);
  assert.deepEqual({ ...B.bulkControls(e, []), cap: 0 }, { approve: false, reject: false, overCap: false, cap: 0, count: 0 });
  assert.equal(B.bulkControls(e, ["1", "2"]).approve, true);
  const rejectOnly = env({ rows: [row("9", { actions: ["reject"], blocked: "refused_file", blocked_reason: "The file was refused — it can only be rejected" })] });
  assert.deepEqual([B.bulkControls(rejectOnly, ["9"]).approve, B.bulkControls(rejectOnly, ["9"]).reject], [false, true]);
  const small = env({ limits: { max_items: 2 } });
  const c = B.bulkControls(small, ["1", "2", "3"]);
  assert.deepEqual([c.approve, c.reject, c.overCap], [false, false, true]);
  assert.match(view(small, { selected: ["1", "2", "3"] }), /At most 2 per request/);
});

test("ONE confirmation names the count and lists every selected item; reject needs a note", () => {
  const e = env();
  const a = B.confirmation(e, ["1", "2", "6"], "approve");
  assert.equal(a.title, "Approve 3 items?");
  assert.equal(a.items.length, 3);
  assert.equal(a.canConfirm, true);
  const r0 = B.confirmation(e, ["1"], "reject", "  ");
  assert.deepEqual([r0.title, r0.canConfirm, r0.noteRequired], ["Reject 1 item?", false, true]);
  assert.equal(B.confirmation(e, ["1"], "reject", "wrong custodian").canConfirm, true);
  const html = renderToStaticMarkup(h(ConfirmView, { envelope: e, selected: ["1", "2"], decision: "approve", note: "",
    onNote: noop, onConfirm: noop, onCancel: noop, busy: false }));
  assert.match(html, /Approve 2 items\?/);
  assert.match(html, /Fixture Household One · \*\*\*\*001/);
  assert.match(html, /Fixture Household One · \*\*\*\*002/);
  const rh = renderToStaticMarkup(h(ConfirmView, { envelope: e, selected: ["1"], decision: "reject", note: "",
    onNote: noop, onConfirm: noop, onCancel: noop, busy: false }));
  assert.match(rh, /<textarea/);
  assert.match(rh, /<button type="button"[^>]*disabled=""[^>]*>Reject 1<\/button>/);
});

test("the request body carries the type, the ids, the decision and the trimmed note — never an org id", () => {
  const b = B.requestBody(env(), ["1", "2"], "reject", "  not this one ");
  assert.deepEqual(b, { type: "authority", item_ids: ["1", "2"], decision: "reject", note: "not this one" });
  assert.deepEqual(B.requestBody(env(), ["1"], "approve", ""), { type: "authority", item_ids: ["1"], decision: "approve" });
  const lines = B.resultLines(env(), { results: [{ id: "1", result: "approved", result_label: "Approved" },
    { id: "zz", result: "refused", reason: "Not found" }] });
  assert.deepEqual(lines.map((l) => [l.label, l.reason]), [["Approved", null], ["Refused", "Not found"]]);
});

test("held-away rows expand to their lines and totals", () => {
  const ha = env({ type: "held_away", rows: [row("h", { type: "held_away", subtype: "manual", from_label: null,
    to_label: "2 line(s) as of 2026-11-04", lines: { positions: [
      { label: "PE Fixture Vineyard", quantity: "1", market_value: "900000.00" },
      { label: "PE Fixture Art", quantity: "2", market_value: "1.25E+5" }], lots: [], transactions: [], rejects: [] },
    totals: { rows_read: 2, rows_accepted: 2, rows_rejected: 0, positions: 2, lots: 0, transactions: 0,
      market_value: "1025000.00" } })] });
  const closed = view(ha);
  assert.match(closed, /Show lines and totals/);
  assert.doesNotMatch(closed, /PE Fixture Vineyard/);
  const open = view(ha, { expanded: "h" });
  assert.match(open, /PE Fixture Vineyard/);
  assert.match(open, /\$900,000\.00/);
  assert.match(open, /\$125,000\.00/, "fixed-point, never scientific notation");
  assert.match(open, /total \$1,025,000\.00/);
});

test("entry points: 'N pending — review in bulk' only for more than one decidable item", () => {
  const s = (a, h2) => ({ link: "/portfolio/approvals", types: { authority: { pending: 9, decidable: a },
    held_away: { pending: 9, decidable: h2 } } });
  assert.equal(B.pendingLink(s(1, 0)), null);
  assert.equal(B.pendingLink(null), null);
  assert.equal(B.pendingLink({ types: { authority: { decidable: 5 } } }), null, "no server link → nothing");
  assert.deepEqual(B.pendingLink(s(3, 2)), { count: 5, text: "5 pending — review in bulk",
    href: "/portfolio/approvals?type=authority" });
  assert.equal(B.pendingLink(s(0, 4)).href, "/portfolio/approvals?type=held_away");
  assert.equal(B.pendingLink(s(3, 2), "held_away").count, 2);
  assert.match(renderToStaticMarkup(h(BulkPendingLinkView, { summary: s(2, 0) })), /2 pending — review in bulk/);
  assert.equal(renderToStaticMarkup(h(BulkPendingLinkView, { summary: s(1, 0) })), "");
  const inbox = renderToStaticMarkup(h(MyTasksView, { payload: { actionable: [{ id: "t", title: "T", kind: "k" }],
    waiting_on_others: [] }, footer: h(BulkPendingLinkView, { summary: s(2, 1) }) }));
  assert.match(inbox, /3 pending — review in bulk/);
});

test("the three entry points mount the link; the menu lists Approvals behind the API's own keys (strict)", () => {
  assert.match(src("app/portfolio/households/[id]/page.js"), /<BulkPendingLink householdId=\{id\} \/>/);
  assert.match(src("components/proposals/AccountsList.jsx"), /<BulkPendingLink \/>/);
  assert.match(src("components/dashboard/MyTasks.jsx"), /<BulkPendingLink \/>/);
  const item = menu.MENU_ITEMS.find((i) => i.href === "/portfolio/approvals");
  assert.ok(item && item.group === "portfolio");
  const me = (perms) => ({ status: "active", account_role: "member", permissions: perms });
  assert.equal(menu.canAccess(me(["approve_authority"]), item.gate), true);
  assert.equal(menu.canAccess(me(["view_held_away"]), item.gate), true);
  assert.equal(menu.canAccess(me(["view_portfolio"]), item.gate), false);
  assert.equal(menu.canAccess(null, item.gate), false);
  assert.equal(menu.canAccess({ status: "active", account_role: "super_admin" }, item.gate), true);
  // no label is hardcoded in the screen: every label comes from the envelope
  const screen = src("components/approvals/BulkApprove.jsx");
  for (const l of ["Authority & custody", "Held-away entries", "Household authority", "You made this"]) {
    assert.ok(!screen.includes(l), `${l} is not hardcoded in BulkApprove.jsx`);
  }
  assert.doesNotMatch(screen, /org_id/);
  assert.doesNotMatch(screen, /\|\|\s*DEFAULT/);
});
