// REP7B — authority (read-only | advise | managed) and custody. The REAL
// components rendered with react-dom/server from envelopes shaped exactly as
// routers/authority.py, routers/account_models.py and routers/xray.py return
// them. Every change / approve / trade control FAILS CLOSED: none without the
// server's own flag being exactly true AND the action in the server's list;
// none the authority × custody matrix does not allow (the server lists only
// those, and nothing here adds one).
import assert from "node:assert/strict";
import { register } from "node:module";
import test from "node:test";

register("../market/jsxLoader.mjs", import.meta.url);

const { createElement: h } = await import("react");
const { renderToStaticMarkup } = await import("react-dom/server");
const lib = await import("../../lib/authority.mjs");
const { default: AuthorityPanel } = await import("../../components/authority/AuthorityPanel.jsx");
const { default: OneOffForm } = await import("../../components/authority/OneOffForm.jsx");
const { default: XrayView } = await import("../../components/xray/XrayView.jsx");

const render = (el) => renderToStaticMarkup(el);
const text = (html) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&")
  .replace(/&quot;/g, '"').replace(/\s+/g, " ");
const buttons = (html) => (html.match(/<button[^>]*>[^<]*<\/button>/g) || []).map((b) => text(b).trim());

const MATRIX = [];
for (const a of ["read_only", "advise", "managed"]) {
  for (const c of ["integrated", "other_custodian", "outside"]) MATRIX.push({ authority: a, custody: c, valid: !(a === "managed" && c === "outside") });
}
const VOCAB = {
  authorities: [{ key: "read_only", label: "Read-only — data only" }, { key: "advise", label: "Advise — one-off recommendations the member or another adviser executes" },
    { key: "managed", label: "Managed — Hollisworks runs the model" }],
  custodies: [{ key: "integrated", label: "Integrated custodian" }, { key: "other_custodian", label: "Another custodian — settled DVP" },
    { key: "outside", label: "Outside the platform" }],
  sources: [{ key: "account", label: "Set on this account" }, { key: "household", label: "The household's setting" },
    { key: "default", label: "Default (held-away accounts read-only; others managed)" }],
  statuses: [{ key: "draft", label: "Awaiting a second person's approval" }, { key: "approved", label: "Approved" }, { key: "rejected", label: "Rejected" }],
  matrix: MATRIX, calendar_note: "settlement date = trade date + 1 business day, counting weekends only", editable: ["authority", "effective_from", "reason"],
  inline_editable: [],
};
const P_VIEW = { can_read: true, can_write: false, can_approve: false, is_super_admin: false, read_permission: "view_authority",
  write_permission: "manage_authority", approve_permission: "approve_authority" };
const P_ALL = { ...P_VIEW, can_write: true, can_approve: true };
const BAD = [P_VIEW, undefined, {}, { ...P_ALL, can_write: "true", can_approve: "true" }];
const pending = (actions) => ({ id: "c1", kind: "household", authority: "advise", effective_from: "2026-10-27", reason: "member asked",
  status: "draft", maker_id: "u1", actions });
const hhEnv = (permissions, actions = ["request_change"], changeActions = ["approve", "reject"], editable = VOCAB.editable) => ({
  household: { id: "h1", name: "ZZ Household" },
  current: { authority: null, note: "no household setting — each account takes its default (held-away read-only; others managed)" },
  history: [pending(changeActions)], pending: pending(changeActions),
  accounts: [{ id: "a1", account_number_masked: "*****0001", is_held_away: true, authority: "read_only", custody: "outside", authority_source: "default" }],
  actions, permissions, vocabularies: { ...VOCAB, editable },
});

test("authority panel: no Request / Approve / Reject without the flags AND the actions", () => {
  for (const p of BAD) {
    const b = buttons(render(h(AuthorityPanel, { initialEnvelope: hhEnv(p), kind: "household" })));
    for (const w of ["Request a change", "Approve", "Reject"]) assert.ok(!b.includes(w), `no ${w} for ${JSON.stringify(p)}: ${b}`);
  }
  const none = buttons(render(h(AuthorityPanel, { initialEnvelope: hhEnv(P_ALL, [], []), kind: "household" })));
  for (const w of ["Request a change", "Approve", "Reject"]) assert.ok(!none.includes(w), `no ${w} without the server's action: ${none}`);
  // a lost editable vocabulary fails closed too
  assert.ok(!buttons(render(h(AuthorityPanel, { initialEnvelope: hhEnv(P_ALL, ["request_change"], [], []), kind: "household" }))).includes("Request a change"));
  const b = buttons(render(h(AuthorityPanel, { initialEnvelope: hhEnv(P_ALL), kind: "household" })));
  for (const w of ["Request a change", "Approve", "Reject"]) assert.ok(b.includes(w), `${w} with the flags and the action: ${b}`);
});

test("authority panel: the maker sees no Approve on their own change (the server lists none)", () => {
  const b = buttons(render(h(AuthorityPanel, { initialEnvelope: hhEnv(P_ALL, [], []), kind: "household" })));
  assert.ok(!b.includes("Approve") && !b.includes("Reject"), b.join(","));
});

test("authority panel: labels and sources come from the envelope", () => {
  const t = text(render(h(AuthorityPanel, { initialEnvelope: hhEnv(P_VIEW), kind: "household" })));
  assert.match(t, /no household setting/);
  assert.match(t, /\*\*\*\*\*0001 · held away Read-only — data only Outside the platform Default/);
  assert.match(t, /Advise — one-off recommendations/);
  assert.match(t, /Awaiting a second person's approval/);
  assert.doesNotMatch(t, /bill|fee/i, "nothing about billing");
});

test("custody options never offer managed + outside; receiving fields only for another custodian, last four only", () => {
  const env = { vocabularies: VOCAB };
  assert.deepEqual(lib.custodyOptions(env, "managed").map((c) => c.key), ["integrated", "other_custodian"]);
  assert.deepEqual(lib.custodyOptions(env, "advise").map((c) => c.key), ["integrated", "other_custodian", "outside"]);
  const b = lib.accountChangeBody({ authority_override: "managed", custody: "other_custodian", effective_from: "2026-10-27",
    reason: " r ", receiving: { custodian_name: "ZZ Trust", participant_number: "0123", account_last4: "123456789" } });
  assert.equal(b.receiving.account_last4, "6789");
  assert.equal(b.reason, "r");
  assert.equal(lib.accountChangeBody({ custody: "outside", effective_from: "x", reason: "r", receiving: { custodian_name: "x" } }).receiving, undefined);
});

const acctEnv = (permissions, actions) => ({
  account: { id: "a1", account_number_masked: "*****0001" }, actions, permissions,
  vocabularies: { one_off_executors: [{ key: "member", label: "Member" }, { key: "other_adviser", label: "Other adviser" }],
    one_off_sides: [{ key: "buy", label: "Buy" }, { key: "sell", label: "Sell" }], editable: [] },
});
const PP_ALL = { can_read: true, can_write: true, can_approve: true, is_super_admin: false };

test("one-off form: only on an advise account the server marked, and only with can_write === true", () => {
  const holdings = [{ id: "p1", security_id: "s1", label: "Fund ZZF (ZZF)" }];
  for (const p of [{ ...PP_ALL, can_write: false }, undefined, {}, { ...PP_ALL, can_write: "true" }]) {
    assert.equal(render(h(OneOffForm, { envelope: acctEnv(p, ["propose_one_off"]), holdings })), "", JSON.stringify(p));
  }
  // read-only and managed accounts: the server lists no propose_one_off — nothing renders
  assert.equal(render(h(OneOffForm, { envelope: acctEnv(PP_ALL, ["join", "generate"]), holdings })), "");
  assert.equal(render(h(OneOffForm, { envelope: acctEnv(PP_ALL, []), holdings })), "");
  const t = text(render(h(OneOffForm, { envelope: acctEnv(PP_ALL, ["propose_one_off"]), holdings })));
  assert.match(t, /Propose a one-off trade/);
  assert.match(t, /Member .*Other adviser/);
  assert.doesNotMatch(t, /Custodian trading desk|Broker|Trader/, "advise never offers the desk, a broker or a trader");
});

const XP = { can_read: true, can_write: true, can_join: true, is_super_admin: false, read_permission: "view_replication",
  write_permission: "manage_replication", join_permission: "manage_proposals" };
const fund = (over) => ({ account_id: "a2", account: "*****0002", held_away: true, fund_security_id: "f1", label: "Fund ZZF (ZZF)",
  market_value: "50000.00", as_of: "2026-10-01", viability: { overall: "yes" }, model: { id: "m1", name: "Holdings of ZZF" },
  replicable: true, fee: {}, embedded_gain: {}, transition_tax: {}, break_even: {},
  join: { available: false, reason: "advise covers one-off trades only" }, one_off: { available: true, side: "sell" },
  actions: ["propose_one_off"], ...over });
const xEnv = (permissions, funds) => ({
  xray: { household: { id: "h1", name: "ZZ" }, as_of: "2026-10-27", total_market_value: "50000.00", holdings: [], by_security: [], by_sector: [],
    concentration: { threshold: "0.10", flags: [] }, overlap: [], replica_overlap: [], funds, status_lines: [] },
  runs: [], permissions, actions: [], vocabularies: { held_away_note: "held away — moving the assets comes first", editable: [], inline_editable: [] },
});

test("X-ray: an advise line offers 'Propose a one-off trade' and never 'Open join request'", () => {
  const b = buttons(render(h(XrayView, { initialEnvelope: xEnv(XP, [fund()]) })));
  assert.ok(b.includes("Propose a one-off trade") && !b.includes("Open join request"), b.join(","));
  for (const p of [{ ...XP, can_join: false }, undefined, {}, { ...XP, can_join: "true" }]) {
    assert.ok(!buttons(render(h(XrayView, { initialEnvelope: xEnv(p, [fund()]) }))).includes("Propose a one-off trade"), JSON.stringify(p));
  }
  // a read-only held-away line: no action listed, one_off unavailable — no trade button at all
  const ro = fund({ actions: [], one_off: { available: false }, join: { available: false, reason: "held away — moving the assets comes first" } });
  const b2 = buttons(render(h(XrayView, { initialEnvelope: xEnv(XP, [ro]) })));
  assert.ok(!b2.includes("Propose a one-off trade") && !b2.includes("Open join request"), b2.join(","));
  // the action without one_off.available (or vice versa) fails closed
  assert.ok(!buttons(render(h(XrayView, { initialEnvelope: xEnv(XP, [fund({ one_off: { available: false } })]) }))).includes("Propose a one-off trade"));
  assert.ok(!buttons(render(h(XrayView, { initialEnvelope: xEnv(XP, [fund({ actions: [] })]) }))).includes("Propose a one-off trade"));
});
