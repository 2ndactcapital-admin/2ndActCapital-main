// SECMASTER2 — the Security master SUMMARY and the filtered LIST, the REAL components rendered with
// react-dom/server from envelopes shaped exactly like GET /admin/security-data/master/summary and
// /securities return them. Every count links to its list with the server's own filter; a filter name the
// server does not list never reaches a link or a request; no envelope / no can_read → nothing (fails closed).
import assert from "node:assert/strict";
import { register } from "node:module";
import test from "node:test";

register("../market/jsxLoader.mjs", import.meta.url);

const { createElement: h } = await import("react");
const { renderToStaticMarkup } = await import("react-dom/server");
const { SummaryPanel, SecuritiesList } = await import("../../components/securityData/SecurityMaster.jsx");
const L = await import("../../lib/secmaster2.mjs");

const render = (el) => renderToStaticMarkup(el);
const text = (html) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&").replace(/\s+/g, " ");
const ALLOWED = ["security_type", "fund_structure", "id_type", "missing_id_type", "provider", "field", "missing_field",
  "price_coverage", "has_price"];
const SU = { can_read: true, can_write: true, is_super_admin: true };
const KINDS = [{ key: "ticker_conflict", label: "Ticker held by a security on another exchange (or with none)" }];
const SUMMARY = {
  total: 40,
  by_type: [{ security_type: "fund", count: 30, filter: { security_type: "fund" },
    structures: [{ structure: "mutual_fund", count: 20, filter: { security_type: "fund", fund_structure: "mutual_fund" } },
      { structure: "not_stated", count: 10, filter: { security_type: "fund", fund_structure: "not_stated" } }] },
  { security_type: "equity", count: 10, filter: { security_type: "equity" }, structures: [] }],
  identifiers: [{ id_type: "sec_class_id", securities: 20, identifiers: 20, missing: 20, filter: { id_type: "sec_class_id" },
    missing_filter: { missing_id_type: "sec_class_id" } }],
  providers: [{ provider_key: "sec_mutual_funds", securities: 22, filter: { provider: "sec_mutual_funds" } }],
  attributes: [{ field: "fund_category", securities: 12, missing: 28, filter: { field: "fund_category" },
    missing_filter: { missing_field: "fund_category" } }],
  prices: [{ price_coverage: "unknown", securities: 40, with_price: 0, filter: { price_coverage: "unknown" },
    with_price_filter: { price_coverage: "unknown", has_price: "true" } }],
  review: { open: 3, by_kind: [{ kind: "ticker_conflict", label: "x", open: 3, review_filter: { status: "open", kind: "ticker_conflict" } }] },
  jobs: [{ job: "security_master_attributes", status: "failed", finished_at: "2026-11-06T20:10:40Z" }],
};
const env = (over = {}) => ({ summary: SUMMARY, list_filters: ALLOWED, permissions: SU, vocabularies: { review_kinds: KINDS }, ...over });

test("summary: every count is the server's number and links to its own filtered list", () => {
  const html = render(h(SummaryPanel, { envelope: env() }));
  const t = text(html);
  for (const w of ["40", "mutual fund: 20", "sec_class_id", "sec_mutual_funds", "fund category", "30%", "failed"]) assert.ok(t.includes(w), w);
  for (const href of ["?security_type=fund", "?security_type=fund&amp;fund_structure=mutual_fund", "?id_type=sec_class_id",
    "?missing_id_type=sec_class_id", "?provider=sec_mutual_funds", "?field=fund_category", "?missing_field=fund_category",
    "?price_coverage=unknown&amp;has_price=true", "?review_kind=ticker_conflict"]) {
    assert.ok(html.includes(`href="/admin/security-data/master${href}"`), href);
  }
});

test("summary fails closed: no envelope, no can_read exactly true → nothing; unlisted filters → no link", () => {
  for (const e of [null, {}, { summary: SUMMARY }, env({ permissions: { can_read: "true" } }), env({ permissions: {} })]) {
    assert.equal(render(h(SummaryPanel, { envelope: e })), "", JSON.stringify(e && e.permissions));
  }
  const html = render(h(SummaryPanel, { envelope: env({ list_filters: undefined }) }));
  assert.ok(html.includes("summary-panel") && !html.includes("?security_type="), "no server filter list → plain numbers, no links");
  const noKind = render(h(SummaryPanel, { envelope: env({ vocabularies: { review_kinds: [] } }) }));
  assert.ok(!noKind.includes("review_kind="), "a review kind the server's vocabulary does not name is not linked");
});

test("link helpers keep only the server's filter names", () => {
  assert.equal(L.listHref({ security_type: "fund", org_id: "x", evil: "1" }, ALLOWED), "/admin/security-data/master?security_type=fund");
  assert.equal(L.listHref({ org_id: "x" }, ALLOWED), null);
  assert.equal(L.listHref({ security_type: "fund" }, null), null);
  assert.equal(L.listRequest({ provider: "sec_mutual_funds", limit: "99999", junk: "1" }, ALLOWED),
    "/api/admin/security-data/master/securities?provider=sec_mutual_funds&limit=100&offset=0");
  assert.equal(L.listRequest({}, ALLOWED), null);
  assert.equal(L.reviewHref({ kind: "nope" }, KINDS), null);
  assert.equal(L.pct(12, 40), "30%");
  assert.equal(L.pct(1, 0), "—");
});

test("list: the server's rows and total; nothing without can_read or rows", () => {
  const list = { rows: [{ id: "g1", name: "FXAIX (SEC share class C000100045)", security_type: "fund", price_coverage: "unknown",
    identifiers: { ticker: "FXAIX", sec_class_id: "C000100045" } }], total: 20, filters: { fund_structure: "mutual_fund" },
  permissions: SU };
  const t = text(render(h(SecuritiesList, { envelope: list })));
  for (const w of ["FXAIX (SEC share class C000100045)", "ticker FXAIX", "sec_class_id C000100045", "20 securities", "first 1 shown",
    "fund structure: mutual_fund"]) assert.ok(t.includes(w), w);
  for (const e of [null, { ...list, permissions: {} }, { permissions: SU }]) assert.equal(render(h(SecuritiesList, { envelope: e })), "");
});
