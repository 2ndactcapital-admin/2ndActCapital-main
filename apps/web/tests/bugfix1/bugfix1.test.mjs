// BUGFIX1 — the REAL components rendered with react-dom/server from
// API-shaped payloads. Each test first shows the defect on the OLD logic
// (reproduced inline, quoted from the pre-sprint code), then the fix.
import assert from "node:assert/strict";
import { register } from "node:module";
import test from "node:test";

register("../market/jsxLoader.mjs", import.meta.url);

const { createElement: h } = await import("react");
const { renderToStaticMarkup } = await import("react-dom/server");
const { default: CommitmentProgress } = await import("../../components/spv/CommitmentProgress.jsx");
const { commitmentView } = await import("../../lib/spvCommitment.mjs");
const parts = await import("../../components/dashboard/briefParts.jsx");
const { default: SearchResultList } = await import("../../components/documents/SearchResultList.jsx");
const { default: ComingSoon } = await import("../../components/ComingSoon.jsx");
const { default: InstitutionPicker } = await import("../../components/admin/InstitutionPicker.jsx");
const { default: CommitmentsList } = await import("../../components/portfolio/CommitmentsList.jsx");
const { default: EmptyHoldingsHint } = await import("../../components/portfolio/EmptyHoldingsHint.jsx");
const { default: StatusBadge } = await import("../../components/marketplace/StatusBadge.jsx");
const { default: StageBar } = await import("../../components/marketplace/StageBar.jsx");
const { pipelineView } = await import("../../lib/dealLifecycle.mjs");
const menu = await import("../../lib/menuVisibility.mjs");

const render = (el) => renderToStaticMarkup(el);
const text = (html) => html.replace(/<[^>]+>/g, "").replace(/&#x27;/g, "'").replace(/&amp;/g, "&");

// The server's commitment_summary for "My first SPV": $1.34M soft vs $1M target.
const SUMMARY = {
  total_committed: 1340000, soft_committed: 1340000, hard_committed: 0,
  soft_count: 2, hard_count: 0, target_raise: 1000000, pct_of_target: 134, over_target: true,
  labels: { soft: "Soft (indicated)", hard: "Hard (signed or funded)", over_target: "over target" },
};

test("SPV-01: old paths showed 100% (cap table) and $0 / 0% (list card); one summary shows 134%", () => {
  // OLD cap table: Math.min(100, round(total/target*100)) — SPVSubscriptionsTab.jsx pre-BUGFIX1.
  const oldCap = Math.min(100, Math.round((1340000 / 1000000) * 100));
  // OLD list card: spv.total_committed was never returned -> `|| 0`.
  const oldList = Math.min(100, Math.round(((undefined || 0) / 1000000) * 100));
  assert.equal(oldCap, 100);
  assert.equal(oldList, 0);
  const html = render(h(CommitmentProgress, { commitment: SUMMARY }));
  assert.match(text(html), /\$1,340,000 committed/);
  assert.match(text(html), /134% of \$1,000,000 target/);
  assert.match(text(html), /over target/);
  assert.match(html, /width:100%/, "the drawn bar stops at 100%; the number does not");
  assert.match(text(render(h(CommitmentProgress, { commitment: SUMMARY }))), /Soft \(indicated\): \$1,340,000 \(2\)/);
  assert.equal(commitmentView(SUMMARY).pctText, "134% of $1,000,000 target");
});

test("SPV-01: the list card and the dashboard card render the SAME summary text", () => {
  const card = text(render(h(CommitmentProgress, { commitment: SUMMARY, compact: true })));
  const dash = text(render(h(parts.SPVActivityBlock, {
    data: { spvs: [{ spv_id: "s1", spv_name: "My first SPV", status: "open", commitment: SUMMARY }],
      soft_subscriptions: 2 },
  })));
  for (const s of ["$1,340,000 committed", "134% of $1,000,000 target"]) {
    assert.ok(card.includes(s) && dash.includes(s), s);
  }
});

test("DASH-03: one line per SPV, and '2 soft subscriptions pending' with its spaces", () => {
  const html = render(h(parts.SPVActivityBlock, {
    data: {
      spvs: [
        { spv_id: "a", spv_name: "My first SPV", status: "open", commitment: SUMMARY },
        { spv_id: "b", spv_name: "TEST-QA SPV", status: "open", commitment: null },
      ],
      soft_subscriptions: 2,
    },
  }));
  assert.equal((html.match(/data-testid="spv-line"/g) || []).length, 2);
  assert.equal((text(html).match(/My first SPV/g) || []).length, 1);
  assert.match(html, /<p[^>]*data-testid="soft-line"[^>]*>2 soft subscriptions pending<\/p>/);
});

test("DASH-01: the pipeline card lists the lifecycle labels in lifecycle order", () => {
  const stages = [
    { status: "draft", label: "Sourced", order: 1, count: 3 },
    { status: "submitted", label: "Submitted", order: 2, count: 0 },
    { status: "under_review", label: "Under Review", order: 3, count: 2 },
    { status: "active", label: "Active", order: 4, count: 2 },
  ];
  const t = text(render(h(parts.PipelineBlock, { data: { stages } })));
  assert.ok(t.indexOf("Sourced") < t.indexOf("Under Review") && t.indexOf("Under Review") < t.indexOf("Active"));
  assert.ok(!t.includes("Submitted"), "zero-count stages are not drawn");
  // The marketplace bar renders the SAME rows with the SAME labels.
  const bar = text(render(h(StageBar, { stageSummary: stages.map((s) => ({ ...s, stage: `k_${s.status}` })) })));
  for (const l of ["Sourced", "Under Review", "Active"]) assert.ok(bar.includes(l), l);
  // The badge shows the lifecycle label when the API sends one.
  assert.equal(text(render(h(StatusBadge, { status: "draft", label: "Sourced" }))), "Sourced");
});

test("R2-MKT-05 / MKT-03: archive needs confirmation; un-archive shows the prior stage; drafts can submit", () => {
  const stages = ["draft", "submitted", "under_review", "active", "closed", "archived"]
    .map((s, i) => ({ status: s, key: `deal_stage_${i + 1}`, label: s === "draft" ? "Sourced" : s, order: i + 1 }));
  const archived = pipelineView({ status: "archived", stages, next: [], unarchive_to: "submitted",
    unarchive_to_label: "Submitted", missing_for_submit: [] }, {});
  assert.equal(archived.canArchive, false);
  assert.deepEqual(archived.unarchive, { status: "submitted", label: "Submitted" });
  const draft = pipelineView({ status: "draft", stages, next: ["submitted"],
    missing_for_submit: [{ field: "target_raise", label: "Target raise" }] }, {});
  assert.equal(draft.canSubmit, true);
  assert.equal(draft.next, null, "Submit has its own confirmed flow, not 'Advance'");
  assert.deepEqual(draft.missing.map((m) => m.label), ["Target raise"]);
  assert.ok(!draft.stages.some((s) => s.status === "archived"), "Archived is not drawn as a step");
});

test("DASH-02: a to-do with an href renders as a link; without one, plain text", () => {
  const linked = render(h(parts.TodoItem, { item: { id: "t", title: "Complete profile: IBM", href: "/crm?q=IBM" } }));
  assert.match(linked, /<a href="\/crm\?q=IBM"[^>]*data-testid="todo-link"/);
  const plain = render(h(parts.TodoTitle, { item: { id: "t", title: "x" } }));
  assert.equal(plain, "x");
});

test("R2-DOC-03: every search result names its source and links to its record", () => {
  const rows = [
    { document_id: "1", source: "deal", source_label: "Deal", parent_name: "Stonegate", href: "/marketplace/d?tab=documents", original_filename: "a.txt", matched_on: ["filename"] },
    { document_id: "2", source: "entity", source_label: "Entity", parent_name: "Hargrove", href: "/crm/e?tab=documents", original_filename: "b.pdf", matched_on: ["category"] },
    { document_id: "3", source: "spv", source_label: "SPV", parent_name: "My first SPV", href: "/spvs/s?tab=documents", original_filename: "c.docx", matched_on: ["filename"] },
  ];
  const html = render(h(SearchResultList, { results: rows, note: "Not yet indexed for meaning search — found by its words only." }));
  for (const [src, href] of [["Deal", "/marketplace/d?tab=documents"], ["Entity", "/crm/e?tab=documents"], ["SPV", "/spvs/s?tab=documents"]]) {
    assert.ok(text(html).includes(src), src);
    assert.ok(html.includes(`href="${href.replace(/&/g, "&amp;")}"`), href);
  }
  assert.equal((html.match(/data-testid="index-note"/g) || []).length, 3);
});

test("DEAD-LINKS: comingSoon items are absent from every persona's menu; the page says Coming soon", async () => {
  const { PERSONAS } = await import("../../../api/scripts/navregroup_personas.mjs");
  assert.deepEqual([...menu.COMING_SOON].sort(), ["/community", "/insurance", "/portfolio-reporting"]);
  for (const [name, me] of Object.entries(PERSONAS)) {
    const hrefs = [
      ...menu.visibleMenuItems(me).map((i) => i.href),
      ...menu.visibleSections(me).flatMap((s) => s.entries.flatMap((e) => e.tabHrefs)),
      ...menu.visibleAdminGroups(me).flatMap((g) => g.tabs.map((t) => t.href)),
    ];
    for (const href of menu.COMING_SOON) assert.ok(!hrefs.includes(href), `${name}: ${href}`);
  }
  const html = render(h(ComingSoon, { label: menu.labelFor("/insurance") }));
  assert.match(text(html), /Coming soon/);
  assert.match(text(html), /Insurance/);
});

test("EDGAR: the Institutions table has a header row with years and Total, numbers right-aligned", () => {
  const rows = [{ name: "Wells Fargo", years: { 2025: 500, 2026: 571 }, total: 1071,
    filers: [{ cik: "72971", name: "WELLS FARGO & CO", role: "issuer", years: { 2025: 500, 2026: 571 }, total: 1071 }] }];
  const html = render(h(InstitutionPicker, { rows, selected: new Set(), onToggle() {}, onAll() {}, onNone() {} }));
  const head = html.match(/<thead[\s\S]*?<\/thead>/)[0];
  assert.deepEqual(text(head).replace(/Select/, "").match(/Institution|Entities|2025|2026|Total/g),
    ["Institution", "Entities", "2025", "2026", "Total"]);
  assert.match(html, /text-right tabular-nums[^>]*>1,071</);
});

test("PORT-01 / PORT-02: empty-holdings hint and the commitments list", () => {
  const hint = render(h(EmptyHoldingsHint, { hint: { message: "Holdings appear here once account data is imported from the custodian.", href: "/portfolio", href_label: "View your investments" } }));
  assert.match(text(hint), /imported from the custodian/);
  assert.match(hint, /href="\/portfolio"/);
  assert.equal(render(h(EmptyHoldingsHint, { hint: null })), "");
  const list = render(h(CommitmentsList, { rows: [{ id: "c1", name: "Hargrove Family Trust", fund: "Fund III", commitment: "1000000", called: "250000", remaining: "750000", href: "/portfolio/commitments/c1" }] }));
  assert.match(text(list), /NameFundCommitmentCalledRemaining/);
  assert.match(list, /href="\/portfolio\/commitments\/c1"/);
  assert.match(text(list), /\$750,000/);
});
