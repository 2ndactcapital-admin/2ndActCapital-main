// REVIEWQ1 — the Security master Review queue: per-row choice (Use incoming · Use existing · Keep both) + one Run, bulk
// by filter, Known discrepancies with Move back. The REAL components rendered with react-dom/server from envelopes
// shaped exactly like GET /admin/security-data/master/review (status open / known) returns them; the pure helpers the
// click handlers run (lib/reviewq1.mjs). Every write control fails CLOSED: a flag must be literally true and a choice
// must be listed in vocabularies.choices AND in the row's own `choices`.
import assert from "node:assert/strict";
import { register } from "node:module";
import test from "node:test";

register("../market/jsxLoader.mjs", import.meta.url);

const { createElement: h } = await import("react");
const { renderToStaticMarkup } = await import("react-dom/server");
const { ReviewChoicesView, KnownDiscrepanciesView, runChoices, runBulkChoice } =
  await import("../../components/securityData/SecurityMaster.jsx");
const L = await import("../../lib/reviewq1.mjs");

const render = (el) => renderToStaticMarkup(el);
const text = (html) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&").replace(/&quot;/g, '"').replace(/\s+/g, " ");
const SU = { can_read: true, can_write: true, can_choose: true, can_bulk: true, can_move_back: true, is_super_admin: true };
const VIEW = { can_read: true, can_write: false, can_choose: false, can_bulk: false, can_move_back: false, is_super_admin: false };
const CHOICES = [{ key: "use_incoming", label: "Use incoming" }, { key: "use_existing", label: "Use existing" }, { key: "keep_both", label: "Keep both" }];
const FILTERS = ["kind", "field", "provider", "q", "home_vs_us", "global_security_id"];
const vocab = (perms) => ({ choices: perms.can_choose === true ? CHOICES : [], review_filters: FILTERS,
  review_kinds: [{ key: "identifier_conflict", label: "Identifier disagrees with the existing security" },
    { key: "type_mismatch", label: "Security type disagrees" }],
  facets: { kind: [{ key: "identifier_conflict", count: 2 }], field: [{ key: "isin", count: 1 }], provider: [{ key: "finance_database", count: 2 }], home_vs_us: 1 } });
const ROWS = [
  { id: "r-isin", kind: "identifier_conflict", kind_label: "Identifier disagrees with the existing security", subject_key: "isin:CA0000000001",
    field: "isin", incoming_value: "CA0000000001", existing_value: "US0000000002", provider_key: "finance_database", security_name: "Maple Corp",
    status: "open", choices: ["use_incoming", "use_existing", "keep_both"], choice_notes: {} },
  { id: "r-type", kind: "type_mismatch", kind_label: "Security type disagrees", subject_key: "security:x", field: "security_type",
    incoming_value: "fund", existing_value: "equity", provider_key: "openfigi", status: "open",
    choices: ["use_incoming", "use_existing"], choice_notes: { keep_both: "a security has one type" } },
];
const queue = (perms = SU, over = {}) => ({ permissions: perms, rows: ROWS, total: 2, vocabularies: vocab(perms), ...over });
const KNOWN = (perms = SU) => ({ permissions: perms, vocabularies: vocab(perms), total: 1, rows: [
  { id: "k1", kind: "identifier_conflict", kind_label: "Identifier disagrees with the existing security", subject_key: "cusip:111111111",
    field: "cusip", incoming_value: "111111111", existing_value: "222222222", provider_key: "finance_database", status: "kept_existing",
    status_label: "Kept existing", decision_note: "pre-split CUSIP", decided_by_email: "admin@example.com", decided_at: "2026-11-12T10:00:00Z",
    seen_again: 3, last_seen: "2026-11-14T07:00:00Z", actions: perms.can_move_back === true ? ["move_back"] : [] }] });

test("Super Admin: each row offers ONLY its server-listed choices, nothing pre-selected; Run (0) disabled; why-not notes", () => {
  const html = render(h(ReviewChoicesView, { envelope: queue() }));
  const t = text(html);
  assert.equal((html.match(/data-testid="review-row"/g) || []).length, 2);
  assert.equal((html.match(/data-testid="review-choice"/g) || []).length, 5, "3 for the ISIN row + 2 for the type row");
  assert.ok(!html.includes("checked"), "nothing pre-selected");
  assert.ok(t.includes("Keep both: a security has one type"), "the server's reason a choice is not offered");
  assert.ok(/<button[^>]*disabled=""[^>]*>Run \(0\)<\/button>/.test(html), "Run disabled with no choice");
  assert.ok(t.includes("Home-market vs US ISIN (1)") && t.includes("2 matching"));
  assert.ok(!html.includes("review-bulk"), "no bulk action without a filter");
});

test("a choice → its radio checked, Run (N), counts per choice", () => {
  const html = render(h(ReviewChoicesView, { envelope: queue(), selections: { "r-isin": "keep_both", "r-type": "use_existing" } }));
  assert.equal((html.match(/checked=""/g) || []).length, 2);
  const t = text(html);
  assert.ok(t.includes("Run (2)") && t.includes("Use incoming 0 · Use existing 1 · Keep both 1"));
  assert.ok(!/disabled=""[^>]*>Run/.test(html));
});

test("bulk: only with a filter and can_bulk exactly true", () => {
  const t = text(render(h(ReviewChoicesView, { envelope: queue(), filter: { home_vs_us: "true" } })));
  assert.ok(t.includes("For all 2 matching:") && t.includes("Keep both…") && t.includes("Use existing…"));
  const off = render(h(ReviewChoicesView, { envelope: queue({ ...SU, can_bulk: "true" }), filter: { home_vs_us: "true" } }));
  assert.ok(!off.includes("review-bulk"), "truthy-but-not-true never grants bulk");
});

test("fails closed: view-only / string flags / missing vocabulary → no choice, no Run, no bulk; lost envelope → nothing", () => {
  for (const env of [queue(VIEW), queue({ ...SU, can_choose: "true" }), { ...queue(), vocabularies: { ...vocab(SU), choices: [] } }]) {
    const html = render(h(ReviewChoicesView, { envelope: env, filter: { kind: "identifier_conflict" }, selections: { "r-isin": "keep_both" } }));
    for (const w of ['type="radio"', "Run (", "review-bulk", "Keep both…"]) assert.ok(!html.includes(w), `${w} in ${JSON.stringify(env.permissions)}`);
    assert.ok(html.includes("Maple Corp"), "still reads");
  }
  for (const e of [null, {}, { permissions: { can_read: "true" } }]) assert.equal(render(h(ReviewChoicesView, { envelope: e })), "");
  // a row listing a choice the vocabulary does not → not rendered
  const odd = queue(SU, { vocabularies: { ...vocab(SU), choices: CHOICES.slice(0, 2) } });
  assert.equal((render(h(ReviewChoicesView, { envelope: odd })).match(/value="keep_both"/g) || []).length, 0);
});

test("Known discrepancies: decision, who, when, note, seen again; Move back only with can_move_back AND the row's action", () => {
  const t = text(render(h(KnownDiscrepanciesView, { envelope: KNOWN() })));
  for (const w of ["Known discrepancies", "Kept existing", "pre-split CUSIP", "admin@example.com", "2026-11-12 10:00",
    "seen again 3× · last 2026-11-14", "Move back to queue"]) assert.ok(t.includes(w), w);
  const v = render(h(KnownDiscrepanciesView, { envelope: KNOWN(VIEW) }));
  assert.ok(!v.includes("Move back") && v.includes("pre-split CUSIP"));
  const noAct = KNOWN(SU); noAct.rows[0].actions = [];
  assert.ok(!render(h(KnownDiscrepanciesView, { envelope: noAct })).includes("Move back"));
  assert.equal(render(h(KnownDiscrepanciesView, { envelope: null })), "");
});

test("Run: confirm shows counts per choice; posts ONLY the rows with a choice; cancel posts nothing", async () => {
  const env = queue();
  const sel = L.choose(L.choose(L.choose({}, "r-isin", "keep_both"), "r-type", "use_incoming"), "r-type", "use_incoming");
  assert.deepEqual(sel, { "r-isin": "keep_both" }, "choosing the same choice twice clears it");
  const sel2 = L.choose(sel, "r-type", "use_existing");
  const posted = [];
  const post = async (url, o) => { posted.push([url, JSON.parse(o.body)]); return { result: { applied_counts: { keep_both: 1, use_existing: 1 }, refused: [] } }; };
  let asked = null;
  const r = await runChoices({ selections: sel2, envelope: env, ask: (m) => { asked = m; return true; }, askNote: () => "first pass", post });
  assert.equal(asked, "Run 2 decisions? Use existing: 1 · Keep both: 1. Rows with no choice stay in the queue.");
  assert.deepEqual(posted, [[L.RUN_API, { choices: [{ id: "r-isin", choice: "keep_both" }, { id: "r-type", choice: "use_existing" }], note: "first pass" }]]);
  assert.equal(L.runResultText(r.result, env), "Applied — Use existing 1 · Keep both 1.");
  posted.length = 0;
  assert.deepEqual(await runChoices({ selections: sel2, envelope: env, ask: () => false, post }), { cancelled: true });
  assert.deepEqual(await runChoices({ selections: {}, envelope: env, ask: () => true, post }), { cancelled: true });
  assert.equal(posted.length, 0);
});

test("bulk: estimate → confirm with the server's count → post with that expected_total; over cap / no filter → nothing", async () => {
  const env = queue();
  const posted = [];
  const post = async (url, o) => {
    posted.push([url, JSON.parse(o.body)]);
    return url === L.BULK_ESTIMATE_API ? { total: 305, applicable: 300, not_offered: 5, choice: "keep_both", over_cap: false, cap: 2000 }
      : { result: { applied_counts: { keep_both: 300 }, refused: [] } };
  };
  let asked = null;
  await runBulkChoice({ choice: "keep_both", filter: { home_vs_us: true, bogus: "x" }, envelope: env, ask: (m) => { asked = m; return true; }, askNote: () => "", post });
  assert.ok(asked.startsWith("Keep both for all 305 matching items? 5 of them do not offer Keep both"), asked);
  assert.deepEqual(posted, [[L.BULK_ESTIMATE_API, { filters: { home_vs_us: "true" }, choice: "keep_both" }],
    [L.BULK_API, { filters: { home_vs_us: "true" }, choice: "keep_both", expected_total: 305 }]]);
  posted.length = 0;
  const big = async (url, o) => { posted.push(url); return { total: 2500, over_cap: true, cap: 2000, choice: "keep_both" }; };
  assert.ok((await runBulkChoice({ choice: "keep_both", filter: { kind: "identifier_conflict" }, envelope: env, ask: () => true, post: big })).error.includes("cap"));
  assert.deepEqual(posted, [L.BULK_ESTIMATE_API], "over the cap: the estimate only, no write");
  assert.ok((await runBulkChoice({ choice: "keep_both", filter: {}, envelope: env, ask: () => true, post: big })).error);
});

test("filters: only the server's names reach a request", () => {
  assert.deepEqual(L.cleanReviewFilter({ kind: "type_mismatch", status: "open", org_id: "x", q: "", home_vs_us: false }, queue()), { kind: "type_mismatch" });
  assert.equal(L.reviewQuery({ home_vs_us: "true", nope: 1 }, queue(), "known"), "/api/admin/security-data/master/review?status=known&home_vs_us=true&limit=500");
  assert.deepEqual(L.cleanReviewFilter({ kind: "x" }, { vocabularies: {} }), {}, "no server list → nothing forwarded");
});
