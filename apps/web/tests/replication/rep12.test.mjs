// REP12 — new issues and the underlying queue: the REAL components rendered
// with react-dom/server from envelopes shaped exactly like the API returns
// them. Every write control (check now, the alert settings, review, dismiss,
// confirm, multi-confirm, reject) must FAIL CLOSED: none without the server's
// own flag being exactly true AND the action listed. The embedded-fee figure
// is shown only as the server states it — "not priced — needs …" otherwise.
import assert from "node:assert/strict";
import { register } from "node:module";
import test from "node:test";

register("../market/jsxLoader.mjs", import.meta.url);

const { createElement: h } = await import("react");
const { renderToStaticMarkup } = await import("react-dom/server");
const L = await import("../../lib/rep12.mjs");
const { NewIssuesView, UnderlyingQueueView } = await import("../../components/replication/NoteChecksWorkspace.jsx");

const text = (html) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&")
  .replace(/&quot;/g, '"').replace(/&gt;/g, ">").replace(/\s+/g, " ");
const render = (el) => text(renderToStaticMarkup(el));

const VOCAB = {
  verdicts: [{ key: "static", label: "Statically replicable" }, { key: "not_replicable", label: "Not replicable with listed options" }],
  archetypes: [{ key: "buffered_note", label: "Buffered note" }],
  statuses: [{ key: "new", label: "New" }, { key: "reviewed", label: "Reviewed" }, { key: "dismissed", label: "Dismissed" }],
  replica_outcomes: [{ key: "prepared", label: "Draft replica prepared" }, { key: "not_built", label: "No replica" }],
  underlying_states: [{ key: "resolved", label: "Confirmed" }, { key: "ambiguous", label: "Proposed — needs confirmation" },
    { key: "unresolved", label: "Not matched" }],
  sorts: [{ key: "newest", label: "Newest first" }, { key: "fee", label: "Embedded-fee estimate" }],
  review_outcomes: [{ key: "reviewed", label: "Mark reviewed" }, { key: "dismissed", label: "Dismiss" }],
  queue_groups: [{ key: "one_proposal", label: "One registry proposal — confirm in one click" },
    { key: "manual", label: "No proposal — open the manual match" }],
  rules: ["No underlying is ever confirmed automatically: a Super Admin confirms each one."],
  new_issue_rule: "A new issue is dated by its 424B2 filing date.",
  editable: ["enabled", "fee_alert_threshold_pct"], inline_editable: [],
};
const VIEW_ONLY = { ...VOCAB, editable: [] };

const priced = { id: "c1", issued_on: "2026-11-03", issued_basis_label: "424B2 filing date", status: "new", verdict: "static",
  replica_outcome: "prepared", replica_link: "/portfolio/replication/notes/replicas/r1", underlyings: "REP12 Index",
  underlying_state: "resolved", not_built_reasons: [], alert_reasons: [{ code: "fee_threshold", text: "Embedded-fee estimate 3.1 per 100 is above the threshold of 2 — estimate" }],
  note: { name: "REP12 Priced Note", link: "/portfolio/replication/notes/n1", issuer: "Issuer Bank", archetype_label: "Buffered note" },
  estimate: { priced: true, embedded_fee_pct: "3.1", text: "3.1 per 100 — estimate" }, actions: ["review", "dismiss"] };
const unpriced = { ...priced, id: "c2", replica_outcome: "not_built", replica_link: null, underlying_state: "ambiguous",
  not_built_reasons: ["Underlying: REP12 Index — not confirmed (ambiguous)."], alert_reasons: [],
  note: { ...priced.note, name: "REP12 Unpriced Note" },
  estimate: { priced: false, embedded_fee_pct: null, text: "not priced — needs the note's price (a note_price quote)" }, actions: [] };
const feedEnv = (perms, { actions = ["run"], vocab = VOCAB, rows = [priced, unpriced] } = {}) => ({
  rows, counts: { new: 2 }, sort: "newest", settings: { enabled: true, fee_alert_threshold_pct: "2", watch_issuers: [], watch_underlyings: [] },
  permissions: { can_read: true, ...perms }, vocabularies: vocab, actions });

const item = (id, over = {}) => ({ id, raw_underlying_text: `REP12 Index ${id}`, link_state: "ambiguous", group: "one_proposal",
  actions: ["confirm", "reject"], proposal: { security_name: "REP12 Index", global_security_id: "s1", kind: "known_index" },
  note: { name: `REP12 Note ${id}`, link: "/portfolio/replication/notes/n1", filer_name: "Issuer Bank" }, ...over });
const queueEnv = (perms, items = [item("e1"), item("e2")]) => ({
  counts: { resolved: 3, ambiguous: 69, unresolved: 28 }, groups: { one_proposal: items,
    manual: [item("m1", { link_state: "unresolved", group: "manual", actions: ["manual_match"], proposal: { kind: "single_name" } })] },
  manual_match: { text: "A manual match names the security on the existing confirm route." },
  permissions: { can_read: true, ...perms }, vocabularies: VOCAB });

test("new issues: check now / settings only with can_write === true, the action and an editable vocabulary", () => {
  const html = render(h(NewIssuesView, { envelope: feedEnv({ can_write: true, can_review: true }) }));
  assert.ok(html.includes("Check now") && html.includes("Save alerts"));
  for (const env of [feedEnv({ can_write: "true" }), feedEnv({ can_write: true }, { actions: [], vocab: VIEW_ONLY }), { rows: [priced] }]) {
    const x = render(h(NewIssuesView, { envelope: env }));
    assert.ok(!x.includes("Check now") && !x.includes("Save alerts"), x.slice(0, 200));
  }
});

test("new issues: review / dismiss only with can_review === true AND the row's own actions", () => {
  const ok = render(h(NewIssuesView, { envelope: feedEnv({ can_review: true }) }));
  assert.equal((ok.match(/Mark reviewed/g) || []).length, 1, "only the row the server lists offers review");
  assert.ok(/\bDismiss\b/.test(ok));
  for (const p of [{}, { can_review: "true" }, { can_review: 1 }]) {
    const x = render(h(NewIssuesView, { envelope: feedEnv(p) }));
    assert.ok(!x.includes("Mark reviewed") && !/\bDismiss\b/.test(x), JSON.stringify(p));
  }
});

test("new issues: the estimate exactly as the server states it; unpriced says what it needs; labels from the vocabulary", () => {
  const html = render(h(NewIssuesView, { envelope: feedEnv({}) }));
  assert.ok(html.includes("3.1 per 100 — estimate"));
  assert.ok(html.includes("not priced — needs the note's price (a note_price quote)"));
  assert.ok(html.includes("Draft replica prepared") && html.includes("Proposed — needs confirmation") && html.includes("Confirmed"));
  assert.ok(html.includes("above the threshold"));
  assert.equal(L.estimateText({}), "—");
  assert.equal(L.isPriced({ estimate: { priced: "true" } }), false);
});

test("underlying queue: confirm / reject only for can_confirm_underlyings === true and the item's actions", () => {
  const ok = render(h(UnderlyingQueueView, { envelope: queueEnv({ can_confirm_underlyings: true }) }));
  assert.equal((ok.match(/\bConfirm\b/g) || []).length, 2);
  assert.ok(ok.includes("Reject") && ok.includes("69") && ok.includes("28"));
  for (const p of [{}, { can_confirm_underlyings: "true" }, { is_super_admin: true }]) {
    const x = render(h(UnderlyingQueueView, { envelope: queueEnv(p) }));
    assert.ok(!/\bConfirm\b/.test(x.replace("confirm in one click", "")) && !x.includes("Reject"), JSON.stringify(p));
  }
  const noAct = render(h(UnderlyingQueueView, { envelope: queueEnv({ can_confirm_underlyings: true }, [item("e3", { actions: [] })]) }));
  assert.ok(!noAct.includes("Reject"));
});

test("lib: multi-confirm keeps only one-proposal edges the server offers; the query drops empty filters", () => {
  const env = queueEnv({ can_confirm_underlyings: true }, [item("e1"), item("e2", { actions: [] })]);
  assert.deepEqual(L.confirmable(env, ["e1", "e2", "m1", "zz"]), ["e1"]);
  assert.deepEqual(L.confirmable(queueEnv({}), ["e1"]), []);
  assert.equal(L.feedQuery({ verdict: "static", issuer: " ", sort: "fee" }), "verdict=static&sort=fee");
  assert.deepEqual(L.splitList("JPMorgan, , Goldman "), ["JPMorgan", "Goldman"]);
  assert.deepEqual(L.rowControls(null, priced), { review: false, dismiss: false });
  assert.deepEqual(L.feedControls(undefined), { run: false, settings: false });
});
