// bulkfetchui3 — the Bulk download Institutions table: sorting on every column,
// filer sub-rows following their institution, the Downloaded / Stored columns,
// Refresh with "as of", Refresh index (+ its note) in the Institutions header
// and NOT in the Time frame card, hidden when not writable, disabled while a
// refresh_index run is active, and no fixed max-h-80. The REAL InstitutionPicker
// is rendered with react-dom/server from the API's own shapes.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { register } from "node:module";
import test from "node:test";

register("../market/jsxLoader.mjs", import.meta.url);

const { createElement: h } = await import("react");
const { renderToStaticMarkup } = await import("react-dom/server");
const { default: InstitutionPicker } = await import("../../components/admin/InstitutionPicker.jsx");
const {
  INSTITUTIONS_REFRESH_MS,
  bulkRunsActive,
  canWrite,
  compareBy,
  indexRefreshState,
  nextSort,
} = await import("../../lib/edgarBulkDownload.mjs");

// The envelope's column vocabulary — sentinels, so a rendered label can only
// have come from the envelope.
const COLUMNS = {
  downloaded: { label: "DL-FROM-ENVELOPE", help: "handled help" },
  stored: { label: "ST-FROM-ENVELOPE", help: "stored help" },
};
const f = (cik, name, total, downloaded, stored, years) => ({ cik, name, role: "issuer", years, total, downloaded, stored });
// Every column orders these three differently; "alpha Bank" is lower-case on
// purpose (case-insensitive names), and Downloaded 100 vs 7 tells numeric from
// lexical order.
const ROWS = [
  { name: "Gamma", years: { 2025: 20, 2026: 0 }, total: 20, downloaded: 100, stored: 5,
    filers: [f("3001", "Gamma One", 12, 60, 3, { 2025: 12 }), f("3002", "gamma two", 8, 40, 2, { 2025: 8 })] },
  { name: "Beta", years: { 2025: 10, 2026: 50 }, total: 60, downloaded: 1, stored: 9,
    filers: [f("2001", "Beta Zed", 5, 0, 1, { 2026: 5 }), f("2002", "Beta Alpha", 40, 1, 7, { 2026: 40 }),
             f("2003", "Beta Mid", 15, 0, 1, { 2025: 10, 2026: 5 })] },
  { name: "alpha Bank", years: { 2025: 30, 2026: 5 }, total: 35, downloaded: 7, stored: 2,
    filers: [f("1001", "Alpha Filer", 35, 7, 2, { 2025: 30, 2026: 5 })] },
];
const ASC = {
  name: ["alpha Bank", "Beta", "Gamma"],
  entities: ["alpha Bank", "Gamma", "Beta"],
  "year:2025": ["Beta", "Gamma", "alpha Bank"],
  "year:2026": ["Gamma", "alpha Bank", "Beta"],
  total: ["Gamma", "alpha Bank", "Beta"],
  downloaded: ["Beta", "alpha Bank", "Gamma"],
  stored: ["alpha Bank", "Gamma", "Beta"],
};
const LABEL = { name: "Institution", entities: "Entities", "year:2025": "2025", "year:2026": "2026", total: "Total",
  downloaded: "DL-FROM-ENVELOPE", stored: "ST-FROM-ENVELOPE" };

const render = (props) =>
  renderToStaticMarkup(h(InstitutionPicker, {
    rows: ROWS, selected: new Set(), onToggle() {}, onAll() {}, onNone() {}, columns: COLUMNS, ...props,
  }));

/** [{kind: "inst"|"filer", name, parent}] in rendered order. */
function bodyRows(html) {
  const body = html.match(/<tbody>([\s\S]*?)<\/tbody>/)[1];
  return [...body.matchAll(/<tr([^>]*)>([\s\S]*?)<\/tr>/g)].flatMap(([, attrs, inner]) => {
    if (attrs.includes('data-testid="institution-row"')) {
      return [{ kind: "inst", name: inner.match(/<\/span>([^<]+)<\/button>/)[1] }];
    }
    if (attrs.includes('data-testid="filer-row"')) {
      return [{ kind: "filer", parent: attrs.match(/data-parent="([^"]+)"/)[1],
        name: inner.match(/<td[^>]*>([^<]+)<span/)[1] }];
    }
    return [];
  });
}
const instOrder = (html) => bodyRows(html).filter((r) => r.kind === "inst").map((r) => r.name);
const header = (html) => html.match(/data-testid="institutions-header"[\s\S]*?<\/div>/)[0];

test("every column sorts both ways with the right comparator (numeric counts, case-insensitive names)", () => {
  for (const [key, asc] of Object.entries(ASC)) {
    assert.deepEqual(instOrder(render({ initialSort: { key, dir: "asc" } })), asc, `${key} asc`);
    assert.deepEqual(instOrder(render({ initialSort: { key, dir: "desc" } })), [...asc].reverse(), `${key} desc`);
  }
  // default: Institution ascending
  assert.deepEqual(instOrder(render({})), ASC.name);
  // a header click toggles: same column flips, a new column starts ascending
  assert.deepEqual(nextSort({ key: "total", dir: "asc" }, "total"), { key: "total", dir: "desc" });
  assert.deepEqual(nextSort({ key: "total", dir: "desc" }, "total"), { key: "total", dir: "asc" });
  assert.deepEqual(nextSort({ key: "total", dir: "desc" }, "stored"), { key: "stored", dir: "asc" });
  // the comparator is numeric: 100 sorts after 7
  assert.ok(compareBy({ key: "downloaded", dir: "asc" })({ name: "a", downloaded: 100 }, { name: "b", downloaded: 7 }) > 0);
});

test("each header is a sort button with aria-sort; the sorted column alone carries the arrow", () => {
  for (const [key, label] of Object.entries(LABEL)) {
    for (const dir of ["asc", "desc"]) {
      const html = render({ initialSort: { key, dir } });
      const ths = [...html.matchAll(/<th([^>]*)>([\s\S]*?)<\/th>/g)].filter(([, a]) => a.includes("data-sort-key"));
      assert.equal(ths.length, 7, "seven sortable headers");
      for (const [, attrs, inner] of ths) {
        const k = attrs.match(/data-sort-key="([^"]+)"/)[1];
        assert.match(inner, /<button type="button"/);
        if (k === key) {
          assert.match(attrs, new RegExp(`aria-sort="${dir === "asc" ? "ascending" : "descending"}"`));
          assert.match(inner, new RegExp(`${label}<span aria-hidden="true">${dir === "asc" ? "▲" : "▼"}</span>`));
        } else {
          assert.match(attrs, /aria-sort="none"/);
          assert.doesNotMatch(inner, /[▲▼]/);
        }
      }
    }
  }
});

test("filer sub-rows stay under their institution and sort the same way among themselves", () => {
  const open = ROWS.map((r) => r.name);
  for (const sort of [{ key: "total", dir: "desc" }, { key: "name", dir: "asc" }, { key: "downloaded", dir: "asc" }]) {
    const rows = bodyRows(render({ initialSort: sort, initialOpen: open }));
    let current = null;
    const seen = {};
    for (const r of rows) {
      if (r.kind === "inst") current = r.name;
      else {
        assert.equal(r.parent, current, `${r.name} sits under ${current} (${sort.key})`);
        (seen[current] ??= []).push(r.name);
      }
    }
    for (const inst of ROWS) {
      const expected = [...inst.filers].sort(compareBy(sort)).map((x) => x.name);
      assert.deepEqual(seen[inst.name], expected, `${inst.name} filers by ${sort.key} ${sort.dir}`);
    }
  }
  // spot-check against hand-written expectations
  const rows = bodyRows(render({ initialSort: { key: "total", dir: "desc" }, initialOpen: open }));
  assert.deepEqual(rows.filter((r) => r.parent === "Beta").map((r) => r.name), ["Beta Alpha", "Beta Mid", "Beta Zed"]);
  const byName = bodyRows(render({ initialSort: { key: "name", dir: "asc" }, initialOpen: open }));
  assert.deepEqual(byName.filter((r) => r.parent === "Gamma").map((r) => r.name), ["Gamma One", "gamma two"]);
});

test("Downloaded and Stored render after Total, labelled from the envelope, with each row's numbers", () => {
  const html = render({ initialOpen: ["alpha Bank"] });
  const head = html.match(/<thead[\s\S]*?<\/thead>/)[0];
  const order = ["Institution", "Entities", "2025", "2026", "Total", "DL-FROM-ENVELOPE", "ST-FROM-ENVELOPE"]
    .map((l) => head.indexOf(l));
  assert.ok(order.every((i, n) => i >= 0 && (n === 0 || i > order[n - 1])), `header order ${order}`);
  assert.match(head, /title="handled help"/);
  assert.match(head, /title="stored help"/);
  assert.match(html, /data-col="downloaded">100</);   // Gamma
  assert.match(html, /data-col="stored">9</);         // Beta
  assert.match(html, /data-col="downloaded">7</);     // alpha Bank and its filer
  // no envelope labels -> no invented client-side label
  const bare = renderToStaticMarkup(h(InstitutionPicker, { rows: ROWS, selected: new Set(), onToggle() {}, onAll() {}, onNone() {} }));
  assert.doesNotMatch(bare, /Downloaded|Stored/);
});

test("Refresh with 'as of', then Refresh index + its note, in the header after Select none", () => {
  const html = render({
    onRefresh() {}, asOf: "2026-10-22T14:05:00Z", writable: true, lastIndexed: "2026-10-21", onRefreshIndex() {},
  });
  const hd = header(html);
  const at = (s) => hd.indexOf(s);
  const seq = [at(">Institutions<"), at("Search institutions"), at(">Select all<"), at(">Select none<"),
    at('data-testid="institutions-refresh"'), at("as of "), at('data-testid="refresh-index"'),
    at("Index loaded through 2026-10-21.")];
  assert.ok(seq.every((i, n) => i >= 0 && (n === 0 || i > seq[n - 1])), `header order ${seq}`);
  assert.match(hd, /as of \d{1,2}:\d{2}/);
  assert.match(hd, /data-testid="refresh-index"[^>]*>Refresh index</);
  assert.doesNotMatch(hd, /<button[^>]* disabled=""[^>]*data-testid="refresh-index"/);
});

test("a non-writable envelope renders no Refresh index button and no note", () => {
  for (const envelope of [{ permissions: { can_write: false } }, {}, null, { permissions: { can_write: "true" } }]) {
    const html = render({ onRefresh() {}, writable: canWrite(envelope), lastIndexed: "2026-10-21", onRefreshIndex() {} });
    assert.doesNotMatch(html, /refresh-index|Refresh index|Index loaded through/, JSON.stringify(envelope));
    assert.match(html, /institutions-refresh/);   // the read-only Refresh stays
  }
});

test("an active refresh_index run disables the button with the right label", () => {
  const runs = (status, extra = {}) => ({ rows: [{ id: "r1", run_kind: "refresh_index", status, display_status: status, ...extra }] });
  const cases = [
    [runs("running"), "running", "Index refreshing"],
    [runs("queued"), "queued", "Index refresh queued"],
    [runs("succeeded"), null, "Refresh index"],
    [runs("running", { display_status: "worker_lost" }), null, "Refresh index"],
    [{ rows: [{ id: "b", run_kind: "bulk_fetch", status: "running", display_status: "running" }] }, null, "Refresh index"],
  ];
  for (const [payload, state, label] of cases) {
    assert.equal(indexRefreshState(payload), state);
    const html = render({ writable: true, onRefreshIndex() {}, indexState: indexRefreshState(payload), lastIndexed: "2026-10-21" });
    const btn = html.match(/<button[^>]*data-testid="refresh-index"[^>]*>([^<]*)</);
    assert.equal(btn[1], label);
    assert.equal(/ disabled=""/.test(btn[0]), state !== null, `${label} disabled=${state !== null}`);
  }
});

test("the counts re-read every 60 s only while a bulk run is queued or running", () => {
  assert.equal(INSTITUTIONS_REFRESH_MS, 60000);
  assert.equal(bulkRunsActive({ rows: [{ run_kind: "bulk_fetch", status: "running" }] }), true);
  assert.equal(bulkRunsActive({ rows: [{ run_kind: "bulk_fetch", status: "queued" }] }), true);
  assert.equal(bulkRunsActive({ rows: [{ run_kind: "refresh_index", status: "running" }] }), false);
  assert.equal(bulkRunsActive({ rows: [{ run_kind: "bulk_fetch", status: "succeeded" }] }), false);
  assert.equal(bulkRunsActive(null), false);
  const src = readFileSync(new URL("../../components/admin/EdgarBulkDownload.jsx", import.meta.url), "utf8");
  assert.match(src, /if \(!bulkActive\) return undefined;\s*const id = setInterval\(refreshInstitutions, INSTITUTIONS_REFRESH_MS\)/);
  assert.match(src, /loadBulkInstitutionsAction\(\{ fresh: true \}\)/);
});

test("Refresh index is gone from the Time frame card; no fixed max-h-80; the table fills its box", () => {
  const tab = readFileSync(new URL("../../components/admin/EdgarBulkDownload.jsx", import.meta.url), "utf8");
  const picker = readFileSync(new URL("../../components/admin/InstitutionPicker.jsx", import.meta.url), "utf8");
  const timeFrame = tab.slice(tab.indexOf(">Time frame</h3>"), tab.indexOf(">Document kinds</h3>"));
  assert.ok(timeFrame.length > 100, "found the Time frame card");
  assert.doesNotMatch(timeFrame, /Refresh index|Index loaded through|refreshIndex|refreshOutcome/);
  assert.match(tab, /onRefreshIndex=\{writable \? refreshIndex : null\}/);
  assert.doesNotMatch(tab + picker, /max-h-80/);
  const html = render({});
  assert.match(html, /class="[^"]*xl:absolute xl:inset-0[^"]*" data-testid="institutions-card"/);
  assert.match(html, /class="[^"]*max-h-\[70vh\][^"]*overflow-auto[^"]*xl:max-h-none[^"]*" data-testid="institutions-scroll"/);
  assert.match(html, /<thead class="sticky top-0/);
  assert.match(html, /class="[^"]*flex flex-wrap[^"]*" data-testid="institutions-header"/);   // buttons wrap at narrow widths
});
