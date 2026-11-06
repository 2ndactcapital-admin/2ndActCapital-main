// BUGFIX4 — the held-away draft (one per account, no stale fields, amount-only lines, digital assets never matched
// to the master), live / superseded labels, the explicit "Create" in the record picker, the household total's
// controls, Consolidate never blank, the private-investment edit + projection link, the jobs switch, fixed-point
// quantities and the stub-login notice. Envelopes are written by hand in the API's shapes (routers/held_away,
// routers/households, routers/portentry, routers/security_master, routers/admin), never imported from them.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { register } from "node:module";
import test from "node:test";

register("../market/jsxLoader.mjs", import.meta.url);

const { createElement: h } = await import("react");
const { renderToStaticMarkup } = await import("react-dom/server");
const B4 = await import("../../lib/bugfix4.mjs");
const HA = await import("../../lib/heldAway.mjs");
const PE = await import("../../lib/portentry.mjs");
const { PendingPanel } = await import("../../components/admin/PeopleParts.jsx");

const src = (p) => readFileSync(new URL(`../../${p}`, import.meta.url), "utf8");
const VOCAB = { value_only_kinds: ["cash", "private"], no_master_kinds: ["cash", "digital_asset"],
  line_states: [{ key: "live", label: "Live" }, { key: "superseded", label: "Superseded — replaced by a later snapshot" },
    { key: "partly_live", label: "Partly live" }, { key: "draft", label: "Draft" }] };

test("a new line carries NOTHING from the previous one; changing the kind starts over", () => {
  const prev = { asset_kind: "", ticker: "QQQ", cusip: "46090E103", quantity: "5000" };
  const next = B4.blankLine();
  assert.deepEqual(next, { asset_kind: "" });
  const cash = B4.changeKind(prev, "cash");
  assert.deepEqual(cash, { asset_kind: "cash" }, "no ticker / CUSIP survives the switch to cash");
});

test("a stale identifier can never ride along on a cash line (A25's cash saved against QQQ)", () => {
  const stale = { asset_kind: "cash", cusip: "46090E103", ticker: "QQQ", market_value: "1050000" };
  const row = B4.lineRow(stale, VOCAB, HA.manualTermFields, HA.manualRow);
  assert.equal(row.cusip, undefined);
  assert.equal(row.ticker, undefined);
  assert.equal(row.asset_kind, "cash");
  assert.equal(row.market_value, "1050000");
  assert.equal(row.quantity, undefined, "a cash line takes an amount, no quantity");
});

test("cash and value-only lines take an amount with no quantity; others need a quantity", () => {
  assert.ok(!B4.lineFields("cash", VOCAB, HA.manualTermFields).includes("quantity"));
  assert.ok(!B4.lineFields("private", VOCAB, HA.manualTermFields).includes("quantity"));
  assert.ok(B4.lineFields("mutual_fund", VOCAB, HA.manualTermFields).includes("quantity"));
  assert.equal(B4.lineReady({ asset_kind: "cash", market_value: "250000" }, VOCAB), true);
  assert.equal(B4.lineReady({ asset_kind: "cash" }, VOCAB), false);
  assert.equal(B4.lineReady({ asset_kind: "mutual_fund", name: "x" }, VOCAB), false);
});

test("a digital asset shows a symbol but never a CUSIP; the server is told it never matches the master", () => {
  const f = B4.lineFields("digital_asset", VOCAB, HA.manualTermFields);
  assert.ok(f.includes("ticker"));
  assert.ok(!f.includes("cusip"));
  assert.equal(B4.neverMaster("digital_asset", VOCAB), true);
  const row = B4.lineRow({ asset_kind: "digital_asset", name: "Ether", ticker: "ETH", cusip: "X", quantity: "120" },
    VOCAB, HA.manualTermFields, HA.manualRow);
  assert.equal(row.cusip, undefined);
  assert.equal(row.ticker, "ETH");
});

test("a listed line may carry its asset-class key (a master fund has no bucket of its own)", () => {
  const row = HA.manualRow({ ticker: "SCHR", quantity: "200000", taxonomy_key: "taxonomy_sc_2" });
  assert.equal(row.taxonomy_key, "taxonomy_sc_2");
});

test("Save draft: one draft with every line; refused until a replacing snapshot is acknowledged", () => {
  const lines = [{ asset_kind: "", ticker: "AGG", quantity: "10" }, { asset_kind: "cash", market_value: "5" },
    { asset_kind: "private", name: "Club deal", market_value: "400000" }];
  assert.equal(B4.draftSaveState({ lines, asOf: "", vocab: VOCAB }).enabled, false);
  assert.equal(B4.draftSaveState({ lines: [], asOf: "2026-11-04", vocab: VOCAB }).enabled, false);
  assert.equal(B4.draftSaveState({ lines, asOf: "2026-11-04", vocab: VOCAB }).enabled, true);
  const r = B4.draftSaveState({ lines, asOf: "2026-11-04", replaces: "Approving this draft replaces…", acknowledged: false, vocab: VOCAB });
  assert.equal(r.enabled, false);
  assert.match(r.reason, /replaces the earlier snapshot/);
  assert.equal(B4.draftSaveState({ lines, asOf: "2026-11-04", replaces: "x", acknowledged: true, vocab: VOCAB }).enabled, true);
});

test("an import says whether it is live or superseded, and by what", () => {
  const sup = { state: "superseded", live_lines: 0, superseded_lines: 1, superseded_by: ["ab12cd34-0000-0000-0000-000000000000"] };
  const t = B4.importStateText(sup, VOCAB);
  assert.match(t, /Superseded/);
  assert.match(t, /0 live, 1 superseded/);
  assert.match(t, /replaced by ab12cd34/);
  assert.equal(B4.importStateText({}, VOCAB), null);
});

test("the panel renders the state + the replace warning, and saves ONE request with all lines", () => {
  const s = src("components/heldaway/HeldAwayPanel.jsx");
  assert.match(s, /importStateText\(r, v\)/);
  assert.match(s, /r\.replaces/);
  assert.match(s, /rows = lines\.map/, "one request carries every line");
  assert.doesNotMatch(s, /rows: \[manualRow\(row\)\]/, "the one-line-per-draft save is gone");
  assert.match(s, /setRow\(blankLine\(\)\)/, "the next line starts empty");
});

test("quantities render fixed-point, never scientific notation", () => {
  assert.equal(B4.qty("3.25E+6"), "3250000");
  assert.equal(B4.qty("7E+4"), "70000");
  assert.equal(B4.qty("18.5"), "18.5");
  assert.equal(B4.qty(null), "—");
  assert.match(src("components/heldaway/HeldAwayPanel.jsx"), /qty\(l\.quantity\)/);
});

test("the record picker never creates on its own: an explicit Create opens a confirmation", () => {
  assert.equal(B4.createOffer({ allowCreate: false, query: "IBM" }), null);
  assert.equal(B4.createOffer({ allowCreate: true, query: "  " }), null);
  assert.equal(B4.createOffer({ allowCreate: true, query: "IBM", loading: true }), null);
  const o = B4.createOffer({ allowCreate: true, query: "IBM" });
  assert.match(o.label, /Create “IBM”/);
  assert.match(o.confirm, /Create a new record named “IBM”\?/);
  const s = src("components/EntityPicker.jsx");
  assert.doesNotMatch(s, /onMouseDown=\{\(e\) => \{ e\.preventDefault\(\); handleCreate\(\); \}\}/, "no create on mousedown");
  assert.match(s, /setConfirmCreate\(null\); handleCreate\(\)/, "handleCreate runs only from the confirmation");
});

test("household Recalculate renders only from the envelope (fails closed); the total says what it leaves out", () => {
  const env = { permissions: { can: { recalculate: true } }, actions: ["recalculate"] };
  assert.equal(B4.householdControls(env).recalculate, true);
  assert.equal(B4.householdControls({ permissions: { can: { recalculate: true } }, actions: [] }).recalculate, false);
  assert.equal(B4.householdControls({ permissions: { can: { recalculate: "yes" } }, actions: ["recalculate"] }).recalculate, false);
  assert.equal(B4.householdControls(null).recalculate, false);
  const t = B4.totalLines({ gross: "100.00", liabilities: "10.00", net: "90.00", holdings_as_of: "2026-11-04",
    unclassified_value: "7545600.00", unvalued_positions: 2 });
  assert.equal(t.net, "90.00");
  assert.equal(t.unclassified, "7545600.00");
  assert.equal(t.unvalued, 2);
  assert.match(src("app/portfolio/households/[id]/page.js"), /<HouseholdSummary householdId=\{id\} \/>/);
});

test("Consolidate is never blank: it says why", () => {
  assert.match(B4.consolidateMessage({ loaded: false }), /Loading/);
  assert.match(B4.consolidateMessage({ loaded: true, status: 403 }), /replication access/);
  assert.equal(B4.consolidateMessage({ loaded: true, status: 200 }), null);
});

test("private investments: Edit only with can_write AND the row's action; commitment rows link to the projection", () => {
  const env = { permissions: { can_write: true } };
  assert.deepEqual(B4.privateRowControls(env, { actions: ["edit"], projection_path: "/portfolio/commitments/abc" }),
    { edit: true, projection: true });
  assert.equal(B4.privateRowControls({ permissions: { can_write: false } }, { actions: ["edit"] }).edit, false);
  assert.equal(B4.privateRowControls(env, { actions: [] }).edit, false);
  assert.equal(B4.privateRowControls(env, { projection_path: null }).projection, false);
  assert.deepEqual(PE.privateEditPayload({ notes: " n ", vintage_year: "2021", investment_date: "2024-01-02" }, { has_commitment: false }),
    { notes: "n", vintage_year: 2021, investment_date: "2024-01-02" });
  assert.deepEqual(PE.privateEditPayload({ commitment_date: "2021-03-01" }, { has_commitment: true }), { commitment_date: "2021-03-01" });
  const pp = PE.privatePayload({ kind: "hedge_fund", name: "H", amount_invested: "1", vintage_year: "2019", investment_date: "2019-05-01" }, "none");
  assert.equal(pp.vintage_year, 2019);
  assert.equal(pp.investment_date, "2019-05-01");
  assert.equal(PE.portentryAllowed("PATCH", "private-investments/assets/00000000-0000-0000-0000-000000000001"), true);
});

test("the jobs switch: Super Admin + the server's action only; a held run is named", () => {
  const env = { permissions: { is_super_admin: true }, actions: ["switch_schedule"] };
  assert.deepEqual(B4.jobSwitch(env, { trigger_active: false }), { label: "Switch schedule on", next: true, note: null });
  assert.equal(B4.jobSwitch(env, { trigger_active: true }).next, false);
  assert.match(B4.jobSwitch(env, { trigger_active: false, blocked_by_held_run: true, blocked_note: "A held run blocks" }).note, /held run/);
  assert.equal(B4.jobSwitch({ permissions: { is_super_admin: false }, actions: ["switch_schedule"] }, {}), null);
  assert.equal(B4.jobSwitch({ permissions: { is_super_admin: true }, actions: [] }, {}), null);
});

test("People & Access flags a placeholder login prominently, with its sign-in id and contact email", () => {
  const stub = { id: "u1", status: "active", stub_login: true, sign_in_id: "auth0|6a7c8b47", contact_email: "x@y.z",
    email: "auth0|6a7c8b47@placeholder.local", email_is_placeholder: true, actions: ["link"] };
  const n = B4.stubLoginNotice(stub);
  assert.match(n.text, /auth0\|6a7c8b47/);
  assert.match(n.text, /x@y\.z/);
  assert.equal(n.canLink, true);
  assert.equal(B4.stubLoginNotice({ ...stub, stub_login: false }), null);
  const html = renderToStaticMarkup(h(PendingPanel, { pending: [stub], users: [stub], roles: [], onApprove() {},
    onReject() {}, onLink() {}, busy: false, error: null }));
  assert.match(html, /data-stub-login="u1"/);
  assert.match(html, /link it to their existing user/);
  assert.match(src("components/admin/UserManagement.jsx"), /u\.stub_login === true/);
});
