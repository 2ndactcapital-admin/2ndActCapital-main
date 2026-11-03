// REP11 — structured notes: the REAL components rendered with react-dom/server
// from envelopes shaped exactly like the API returns them. Every write control
// (recompute verdicts, build, price, submit, approve, reject, archive) must
// FAIL CLOSED: none without the server's own flag being exactly true AND the
// action listed. A replica cost / embedded fee is shown only as the server
// sends it — "not priced — enter a quote …" or withheld under the
// hypothetical-performance policy — never a placeholder number.
import assert from "node:assert/strict";
import { register } from "node:module";
import test from "node:test";

register("../market/jsxLoader.mjs", import.meta.url);

const { createElement: h } = await import("react");
const { renderToStaticMarkup } = await import("react-dom/server");
const L = await import("../../lib/rep11.mjs");
const { NotesListView, NoteDetailView, ReplicaView } = await import("../../components/replication/NotesWorkspace.jsx");
const { HeldNotes } = await import("../../components/xray/XrayView.jsx");

const text = (html) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&")
  .replace(/&quot;/g, '"').replace(/\s+/g, " ");
const render = (el) => text(renderToStaticMarkup(el));

const VOCAB = {
  verdicts: [{ key: "static", label: "Statically replicable" }, { key: "approximate", label: "Approximately replicable" },
    { key: "not_replicable", label: "Not replicable with listed options" }],
  archetypes: [{ key: "buffered_note", label: "Buffered note" }, { key: "autocallable", label: "Autocallable" }],
  basket_types: [{ key: "single", label: "Single underlying" }, { key: "worst_of", label: "Worst-of" }],
  protection_types: [{ key: "buffer", label: "Buffer" }],
  replica_statuses: [{ key: "draft", label: "Draft" }, { key: "pending_approval", label: "Awaiting a second person's approval" },
    { key: "approved", label: "Approved — the firm's replicable alternative" }],
  counsel_items: ["Tax: the note versus the replica — for counsel."],
  ssvi: { text: "No volatility surface is fitted." },
  editable: ["broad_based_index", "start_level_quote_id"], price_editable: ["start_level_quote_id"],
};
const VIEW_ONLY = { ...VOCAB, editable: [], price_editable: [] };
const verdict = (over = {}) => ({ verdict: "static", verdict_label: "Statically replicable", mapping_label: "Underlying + options",
  reasons: [{ code: "buffer_package", text: "Single underlying, paid at expiry." }], is_preliminary: false, needs_review: false,
  review_reasons: [], ...over });
const row = { global_security_id: "n1", name: "REP11 Note", cusip: "ZZ1", credit_entity: "Issuer Bank", product_archetype: "buffered_note",
  basket_type: "single", underlyings: "Index", verdict: verdict() };
const listEnv = (perms, actions = ["run_verdicts"], vocab = VOCAB) => ({ rows: [row], counts: { static: 1 }, permissions: { can_read: true, ...perms },
  vocabularies: vocab, actions });

const points = [{ underlying_return: "-0.5", note_return: "-0.3", replica_return: "-0.3" },
  { underlying_return: "0", note_return: "0", replica_return: "0" }, { underlying_return: "0.5", note_return: "0.2", replica_return: "0.2" }];
const noteEnv = (perms, actions = ["build"], vocab = VOCAB) => ({
  note: { global_security_id: "n1", name: "REP11 Note", cusip: "ZZ1", product_archetype: "buffered_note", basket_type: "single",
    protection_type: "buffer", protection_pct: "0.2", cap_pct: "0.2", participation_rate: "1", terms_status: "preliminary",
    extraction_confidence: "high", initial_valuation_date: "2026-01-02", final_valuation_date: "2028-01-03", credit_entity: "Issuer Bank",
    filing: { form_type: "424B2", filing_date: "2026-01-01", url: "https://www.sec.gov/x" } },
  verdict: verdict({ is_preliminary: true, preliminary_text: "preliminary — final terms may differ" }),
  underlying: { text: "Index (confirmed)" }, distribution: { known: false, text: "not in the extracted terms — the filing's distribution and fees were not read" },
  payoff: { drawn: true, points, legs: [{ purpose: "Long put at 100% — starts the buffer" }], note: "at expiry, from strikes only" },
  replicas: [], buildable: { ok: true, why: [] }, differences: [{ key: "credit", label: "Issuer credit", text: "An unsecured promise of Issuer Bank." }],
  permissions: { can_read: true, ...perms }, vocabularies: vocab, actions });

const replicaEnv = (perms, actions, comparison = null, vocab = VOCAB) => ({
  replica: { id: "r1", status: "pending_approval", mapping_label: "Underlying + options", outcome_start: "2026-01-02", outcome_end: "2028-01-03",
    start_level: "5000" },
  note: { name: "REP11 Note" }, manual_only: "Nothing trades: every leg is a manual instruction a person carries out.", needs: [],
  legs: [{ id: "l1", instruction: "Buy 1 × FLEX put — manual, through the custodian's options desk." }],
  computed: { rows: [{ term: "cap", stated: "0.2", computed: "0.2", equal: true }] }, payoff: { points }, comparison,
  permissions: { can_read: true, ...perms }, vocabularies: vocab, actions });

test("notes list: no recompute control without can_write === true and the listed action", () => {
  assert.ok(render(h(NotesListView, { envelope: listEnv({ can_write: true }) })).includes("Recompute verdicts"));
  assert.ok(!render(h(NotesListView, { envelope: listEnv({ can_write: "true" }) })).includes("Recompute verdicts"));
  assert.ok(!render(h(NotesListView, { envelope: listEnv({ can_write: true }, []) })).includes("Recompute verdicts"));
  assert.ok(!render(h(NotesListView, { envelope: { rows: [row] } })).includes("Recompute verdicts"));
  const html = render(h(NotesListView, { envelope: listEnv({}) }));
  assert.ok(html.includes("Statically replicable") && html.includes("REP11 Note") && html.includes("Buffered note"));
});

test("note page: build only with can_write, the action and an editable vocabulary; preliminary marked", () => {
  assert.ok(render(h(NoteDetailView, { envelope: noteEnv({ can_write: true }) })).includes("Build a replica"));
  assert.ok(!render(h(NoteDetailView, { envelope: noteEnv({ can_write: true }, []) })).includes("Build a replica"));
  assert.ok(!render(h(NoteDetailView, { envelope: noteEnv({ can_write: true }, ["build"], VIEW_ONLY) })).includes("Build a replica"));
  assert.ok(!render(h(NoteDetailView, { envelope: noteEnv({}) })).includes("Build a replica"));
  const html = render(h(NoteDetailView, { envelope: noteEnv({}) }));
  assert.ok(html.includes("preliminary — final terms may differ"));
  assert.ok(html.includes("identical at all 3 grid points"));
  assert.ok(html.includes("not in the extracted terms") && html.includes("Issuer credit"));
});

test("replica page: approve / reject only with can_approve === true and the listed action", () => {
  assert.ok(render(h(ReplicaView, { envelope: replicaEnv({ can_approve: true }, ["approve", "reject"]) })).includes("Approve"));
  assert.ok(!render(h(ReplicaView, { envelope: replicaEnv({ can_approve: "yes" }, ["approve", "reject"]) })).includes("Approve"));
  assert.ok(!render(h(ReplicaView, { envelope: replicaEnv({ can_approve: true }, []) })).includes("Approve"));
  assert.ok(!render(h(ReplicaView, { envelope: replicaEnv({ can_write: true }, ["approve"]) })).includes("Approve"));
  const view = render(h(ReplicaView, { envelope: replicaEnv({}, ["price", "submit", "archive", "approve", "reject"]) }));
  for (const b of ["Approve", "Reject", "Submit for approval", "Price now", "Archive"]) assert.ok(!view.includes(b), b);
  assert.ok(render(h(ReplicaView, { envelope: replicaEnv({ can_write: true }, ["price", "submit"]) })).includes("Submit for approval"));
  assert.ok(view.includes("manual instruction"));
});

test("pricing: not priced names the gap; priced is an estimate with as-of; withheld under the policy", () => {
  const np = { status: "not_priced", text: "not priced — enter a quote — missing: the note's price (a note_price quote on REP11 Note, % of par)" };
  assert.ok(render(h(ReplicaView, { envelope: replicaEnv({}, [], np) })).includes("missing: the note's price"));
  const pr = { status: "priced", note_price_pct: "98.75", replica_cost_pct: "97.5", embedded_fee_pct: "1.25", as_of: "2026-11-02T15:00:00+00:00", inputs: [] };
  const html = render(h(ReplicaView, { envelope: replicaEnv({}, [], pr) }));
  assert.ok(html.includes("Embedded fee 1.2500 per 100") && html.includes("as of 2026-11-02") && html.includes("estimate"));
  const wh = { withheld: true, text: "no hypothetical-performance policy approved — staff use only" };
  const w = render(h(ReplicaView, { envelope: replicaEnv({}, [], wh) }));
  assert.ok(w.includes("Estimate withheld") && !w.includes("Embedded fee"));
  assert.equal(L.estimateText(null), "not priced — enter a quote");
});

test("X-ray: a held note shows its verdict and the replicable alternative; a member's estimate is withheld", () => {
  const n = { label: "REP11 Note", account: "****0001", security_id: "n1", verdict: verdict(), terms: { archetype_label: "Buffered note", basket_label: "Single underlying",
    cap_pct: "0.2", protection_label: "Buffer", protection_pct: "0.2", final_valuation_date: "2028-01-03", credit_entity: "Issuer Bank" },
    distribution: { known: false, text: "not in the extracted terms" },
    replicable_alternative: { label: "replicable alternative", link: "/portfolio/replication/notes/replicas/r1", mapping_label: "Underlying + options" } };
  const staff = render(h(HeldNotes, { envelope: { xray: { notes: [{ ...n, estimate: { status: "priced", text: "Embedded fee estimate 1.25 per 100 of principal" } }] } } }));
  assert.ok(staff.includes("replicable alternative") && staff.includes("Statically replicable") && staff.includes("Embedded fee estimate 1.25"));
  const member = render(h(HeldNotes, { envelope: { xray: { notes: [{ ...n, estimate: { withheld: true, text: "no hypothetical-performance policy approved — staff use only" } }] } } }));
  assert.ok(member.includes("Statically replicable") && member.includes("Estimate withheld") && !member.includes("1.25"));
  assert.ok(render(h(HeldNotes, { envelope: { xray: { notes: [] } } })).includes("No structured note"));
});

test("lib: pct / per100 / overlay are pure and fail soft", () => {
  assert.equal(L.pct("0.2"), "20.00%");
  assert.equal(L.pct(null), "—");
  assert.equal(L.per100("97.5"), "97.5000");
  assert.equal(L.overlay([]), null);
  const o = L.overlay([{ underlying_return: "0", note_return: "0.1", replica_return: "0" }, { underlying_return: "1", note_return: "0.1", replica_return: "0.1" }]);
  assert.equal(o.diffs, 1);
  assert.deepEqual(L.replicaControls(null), { price: false, submit: false, archive: false, approve: false, reject: false });
  assert.deepEqual(L.noteControls(undefined), { build: false });
});
