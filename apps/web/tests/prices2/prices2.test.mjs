// PRICES2 — the Security master summary's price-refresh line: the REAL SummaryPanel / PriceRefreshLine rendered with
// react-dom/server from an envelope shaped exactly like GET /admin/security-data/master/summary returns it.
// Every word and number is the server's (prices.refresh_status); no control is rendered; no envelope → nothing.
import assert from "node:assert/strict";
import { register } from "node:module";
import test from "node:test";

register("../market/jsxLoader.mjs", import.meta.url);

const { createElement: h } = await import("react");
const { renderToStaticMarkup } = await import("react-dom/server");
const { SummaryPanel, PriceRefreshLine } = await import("../../components/securityData/SecurityMaster.jsx");

const text = (html) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&").replace(/\s+/g, " ");
const REFRESH = {
  covered: 2600, refreshed_today: 1840, day: "2026-11-13", oldest_price_date: "2026-11-11", without_prices: 3,
  label: "covered 2,600 · refreshed today 1,840 · oldest price date 2026-11-11",
  days: [{ price_day: "2026-11-13", status: "running", status_label: "Running", progress_label: "1,840 of 2,600 refreshed",
    calls: 1840, stop_detail: null }],
};
const env = (over = {}) => ({
  permissions: { can_read: true, can_write: false, is_super_admin: true },
  summary: { total: 10, by_type: [], identifiers: [], providers: [], prices: [], attributes: [], review: { open: 0 }, jobs: [] },
  list_filters: [], price_refresh: REFRESH, ...over,
});

test("summary shows the server's 'covered N · refreshed today M · oldest price date D' and the day's progress", () => {
  const html = renderToStaticMarkup(h(SummaryPanel, { envelope: env() }));
  const t = text(html);
  for (const w of ["covered 2,600 · refreshed today 1,840 · oldest price date 2026-11-11", "2026-11-13",
    "1,840 of 2,600 refreshed", "1840 calls", "Running"]) assert.ok(t.includes(w), w);
  assert.ok(html.includes('data-testid="price-refresh-label"'));
  assert.ok(!/<button/.test(renderToStaticMarkup(h(PriceRefreshLine, { refresh: REFRESH }))), "no control in the line");
});

test("a budget stop shows the server's reason; no refresh block → nothing (never a client default)", () => {
  const stopped = { ...REFRESH, days: [{ ...REFRESH.days[0], status: "stopped_budget",
    status_label: "Stopped — the daily call budget is spent (the rest goes first tomorrow)",
    stop_detail: "yahoo_chart's daily call budget is spent; 600 not refreshed today go first tomorrow" }] };
  const t = text(renderToStaticMarkup(h(PriceRefreshLine, { refresh: stopped })));
  assert.ok(t.includes("600 not refreshed today go first tomorrow"));
  assert.equal(renderToStaticMarkup(h(PriceRefreshLine, { refresh: null })), "");
  assert.equal(renderToStaticMarkup(h(PriceRefreshLine, { refresh: { days: [] } })), "");
  const html = renderToStaticMarkup(h(SummaryPanel, { envelope: env({ price_refresh: undefined }) }));
  assert.ok(!html.includes("price-refresh"), "no price_refresh in the envelope → no line");
});
