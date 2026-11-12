/**
 * dailyops1 — the Market Indicators page with a large security master, and the
 * securities picker's search box. Renders the REAL components with
 * react-dom/server (JSX compiled by ./jsxLoader.mjs; components unchanged).
 *
 *   - the catalog loads (ready state, picker rendered) with a large master;
 *   - a search result without price history is disabled with the reason;
 *   - an error still shows the server's own message (catalog and search);
 *   - fail closed: no envelope → no results; no search vocabulary → no box.
 */
import assert from "node:assert/strict";
import { register } from "node:module";
import { test } from "node:test";

register("./jsxLoader.mjs", import.meta.url);

const { createElement: h } = await import("react");
const { renderToStaticMarkup } = await import("react-dom/server");
const { default: MarketIndicatorsView } = await import("../../components/market/MarketIndicatorsView.jsx");
const { default: SelectionPanel } = await import("../../components/market/SelectionPanel.jsx");
const {
  interpretCatalog,
  interpretSecuritySearch,
  securitySearchUrl,
  searchSettings,
  NO_ENVELOPE_MESSAGE,
  SECURITY_SEARCH_ROUTE,
} = await import("../../lib/market/catalogModel.mjs");
const { catalogBody } = await import("./fixtures.mjs");

const TODAY = "2026-11-12";
const SEARCH = { min_chars: 3, max_chars: 50, max_results: 7 }; // test values, not the server's

function bodyWithSearch(extra = {}) {
  const b = catalogBody();
  b.vocabularies.search = { ...SEARCH };
  return Object.assign(b, extra);
}

const PERMS = { can_read: true, can_write: false, is_super_admin: false, read_permission: null, write_permission: null };

function panel(catalog, initialSearch) {
  return renderToStaticMarkup(h(SelectionPanel, { catalog, selection: [], onChange: () => {}, initialSearch }));
}

// ── the catalog with a large security master ───────────────────────────────

test("the catalog loads with a large security master: ready state, picker and search box rendered", () => {
  const body = bodyWithSearch();
  // 12,000 security-master rows — what the page would get even if the server ever sent them all again.
  const big = [];
  for (let i = 0; i < 12000; i += 1) {
    big.push({
      id: `cccccccc-0000-0000-0000-${String(i).padStart(12, "0")}`,
      name: `Fixture security ${i}`,
      short_name: null,
      security_type: i % 2 ? "type_one" : "type_two",
      price_source: "none",
      series_key: null,
      selectable: false,
      unselectable_reason: "reason_code_x",
      color: "#123456",
    });
  }
  body.securities = [...body.securities, ...big];
  const state = interpretCatalog({ ok: true, body });
  assert.equal(state.kind, "ready");
  assert.equal(state.catalog.securities.length, 12004);
  const t0 = Date.now();
  const html = renderToStaticMarkup(
    h(MarketIndicatorsView, { catalogState: state, today: TODAY, initialTab: "grid" }),
  );
  assert.ok(Date.now() - t0 < 20000, "render finished in reasonable time");
  assert.ok(html.includes('data-market-control="selection-panel"'));
  assert.ok(html.includes('data-market-control="security-search"'));
  assert.ok(!html.includes('data-market-state="error"'));
  assert.ok(html.includes("Linked Index One"), "the selectable security is listed");
});

test("the selectable-only catalog (the server's new shape) renders every listed security as selectable", () => {
  const body = bodyWithSearch();
  body.securities = body.securities.filter((s) => s.selectable);
  const state = interpretCatalog({ ok: true, body });
  assert.equal(state.kind, "ready");
  const html = panel(state.catalog);
  assert.ok(html.includes("Linked Index One"));
  assert.ok(!html.includes('data-market="unselectable"'), "no disabled row without a search");
});

// ── search ──────────────────────────────────────────────────────────────────

test("a search result without price history is disabled and shows the reason; a priced one is selectable", () => {
  const catalog = interpretCatalog({ ok: true, body: bodyWithSearch() }).catalog;
  const state = interpretSecuritySearch({
    ok: true,
    body: {
      query: "fix",
      securities: [
        { ...catalog.securities[0], ticker: "LIO" },
        {
          id: "dddddddd-0000-0000-0000-000000000001", name: "Fixture Unpriced Corp", short_name: null,
          security_type: "type_one", price_source: "none", series_key: null, selectable: false,
          unselectable_reason: "no_price_history", color: "#123456", ticker: "FUC", cusip: null,
        },
      ],
      permissions: PERMS,
      vocabularies: {},
    },
  });
  assert.equal(state.kind, "results");
  const html = panel(catalog, { text: "fix", state });
  assert.ok(html.includes('data-market="search-results"'));
  const unpriced = html.split('data-market="unselectable"');
  assert.ok(unpriced.length >= 2, "the unpriced result is an unselectable row");
  const row = unpriced[1].split("</li>")[0];
  assert.ok(/<input[^>]*type="checkbox"[^>]*disabled/.test(row) || /<input[^>]*disabled[^>]*type="checkbox"/.test(row),
    "its checkbox is disabled");
  assert.ok(row.includes("Fixture Unpriced Corp (FUC)"));
  assert.ok(row.includes("no price history"), "the reason is shown as text");
  const results = html.split('data-market="search-results"')[1];
  assert.ok(/Linked Index One \(LIO\)/.test(results), "the priced result is listed");
  const pricedRow = results.split("Linked Index One (LIO)")[0];
  const lastInput = pricedRow.slice(pricedRow.lastIndexOf("<input"));
  assert.ok(!/disabled/.test(lastInput.split(">")[0]), "the priced result's checkbox is enabled");
});

test("a search error shows the server's own message", () => {
  const catalog = interpretCatalog({ ok: true, body: bodyWithSearch() }).catalog;
  const state = interpretSecuritySearch({
    ok: false,
    body: { detail: { message: "Request validation failed", errors: [{ msg: "Server rule text 42" }] } },
  });
  assert.equal(state.kind, "error");
  assert.equal(state.message, "Request validation failed: Server rule text 42");
  const html = panel(catalog, { text: "abc", state });
  assert.ok(html.includes('data-market="search-error"'));
  assert.ok(html.includes("Request validation failed: Server rule text 42"));
});

test("a catalog error still shows the server's message (and the fallback only when there is none)", () => {
  const withMsg = interpretCatalog({ ok: false, body: { detail: "Server says the catalog failed" } });
  assert.deepEqual(withMsg, { kind: "error", message: "Server says the catalog failed" });
  const html = renderToStaticMarkup(h(MarketIndicatorsView, { catalogState: withMsg, today: TODAY }));
  assert.ok(html.includes("Server says the catalog failed"));
  assert.ok(!html.includes('data-market-control="selection-panel"'));
  const noBody = interpretCatalog({ ok: false, body: null });
  assert.equal(noBody.message, "Market data could not be loaded.");
});

test("fail closed: a search response without a read-granting envelope is an error, never results", () => {
  for (const permissions of [undefined, null, { can_read: false }, { can_read: "true" }]) {
    const state = interpretSecuritySearch({ ok: true, body: { query: "abc", securities: [], permissions } });
    assert.deepEqual(state, { kind: "error", message: NO_ENVELOPE_MESSAGE });
  }
  const noList = interpretSecuritySearch({ ok: true, body: { query: "abc", permissions: PERMS } });
  assert.equal(noList.kind, "error");
});

test("fail closed: no search vocabulary in the catalog → no search box", () => {
  const body = catalogBody();
  const catalog = interpretCatalog({ ok: true, body }).catalog;
  assert.equal(searchSettings(catalog), null);
  assert.ok(!panel(catalog).includes('data-market-control="security-search"'));
});

test("the search URL uses the server's limits and the Next.js route; too short or too long → no request", () => {
  const settings = searchSettings(interpretCatalog({ ok: true, body: bodyWithSearch() }).catalog);
  assert.deepEqual(settings, { minChars: 3, maxChars: 50, maxResults: 7 });
  assert.equal(securitySearchUrl("ab", settings), null);
  assert.equal(securitySearchUrl("  ab  ", settings), null);
  assert.equal(securitySearchUrl("x".repeat(51), settings), null);
  assert.equal(securitySearchUrl(" S&P 500 ", settings), `${SECURITY_SEARCH_ROUTE}?q=S%26P%20500`);
  assert.ok(SECURITY_SEARCH_ROUTE.startsWith("/api/market/"));
  assert.equal(securitySearchUrl("abc", null), null);
});
