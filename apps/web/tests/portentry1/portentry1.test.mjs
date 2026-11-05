// PORTENTRY1 — CRM vocabularies from the envelope, the manual-holding row, the direct-asset / private-investment
// payloads, the write-control gate and the proxy allow-list. The vocabulary below is written by hand in the API's
// envelope shape (services/crm_config.build_vocab), never imported from it.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const V = await import("../../lib/crmVocab.mjs");
const P = await import("../../lib/portentry.mjs");
const H = await import("../../lib/heldAway.mjs");
const D = await import("../../lib/crmDetail.mjs");

const VOCAB = {
  entity_statuses: [
    { value: "prospect", label: "Prospect", selectable: true },
    { value: "client", label: "Client", selectable: true },
    { value: "contact", label: "Contact", selectable: true },
    { value: "active", label: "Active", selectable: true },
    { value: "deceased", label: "Deceased", selectable: false },
  ],
  marital_statuses: [{ value: "married", label: "Married", selectable: true },
    { value: "domestic_partnership", label: "Domestic partnership", selectable: true }],
  sub_types_by_type: { llc: [{ value: "Real estate", key: "real_estate", label: "Real estate" },
    { value: "Collectibles", key: "collectibles", label: "Collectibles" }] },
  free_text_sub_type_types: ["corp_other_intl"],
  status_filters: [{ value: "", label: "All" }, { value: "client", label: "Client" }],
};

test("status options: selectable ones from the envelope; deceased only when it is the current value", () => {
  assert.deepEqual(V.statusOptions(VOCAB, "prospect").map((o) => o.value), ["prospect", "client", "contact", "active"]);
  assert.deepEqual(V.statusOptions(VOCAB, "deceased").map((o) => o.value).at(-1), "deceased");
  assert.equal(V.statusLabel(VOCAB, "contact"), "Contact");
  assert.deepEqual(V.statusFilters(VOCAB).map((f) => f.value), ["", "client"]);
});

test("a lost envelope yields NO options — never a client-side default list", () => {
  assert.deepEqual(V.statusOptions(null, null), []);
  assert.deepEqual(V.maritalOptions(undefined), []);
  assert.deepEqual(V.subTypeOptions(null, "llc", null), []);
  assert.deepEqual(V.statusFilters(null), []);
});

test("marital and sub-type options; a legacy sub-type label is kept, not dropped", () => {
  assert.deepEqual(V.maritalOptions(VOCAB).map((o) => o.value), ["married", "domestic_partnership"]);
  assert.equal(V.maritalLabel(VOCAB, "domestic_partnership"), "Domestic partnership");
  assert.deepEqual(V.subTypeOptions(VOCAB, "llc", "Single-member").map((o) => o.value),
    ["Real estate", "Collectibles", "Single-member"]);
  assert.equal(V.freeTextSubType(VOCAB, "corp_other_intl"), true);
  assert.equal(V.freeTextSubType(VOCAB, "llc"), false);
});

test("no page carries the old hard-coded status / sub-type lists", () => {
  const src = readFileSync(new URL("../../lib/entityTypes.js", import.meta.url), "utf-8");
  assert.ok(!/STATUS_OPTIONS|SUBTYPES_BY_TYPE/.test(src));
  for (const f of ["../../components/crm/EntityDetailsForm.jsx", "../../components/crm/NewEntityForm.jsx",
    "../../app/crm/page.js"]) {
    const s = readFileSync(new URL(f, import.meta.url), "utf-8");
    assert.ok(!/STATUS_OPTIONS|STATUS_FILTERS|subTypesFor/.test(s), f);
    assert.ok(/crmVocab/.test(s), `${f} reads the envelope vocabularies`);
  }
});

test("a manual holding row carries its type and terms as strings (Decimal-safe)", () => {
  const row = H.manualRow({ asset_kind: "municipal_bond", name: "NYC GO 5% 2034", quantity: "100000", price: "1.02",
    coupon: "5.000", maturity: "2034-08-01", taxonomy_key: "taxonomy_sc_2" });
  assert.equal(row.asset_kind, "municipal_bond");
  assert.equal(row.coupon, "5.000");
  assert.equal(row.maturity, "2034-08-01");
  assert.equal(typeof row.quantity, "string");
  assert.deepEqual(H.manualTermFields("listed_option"), ["underlying_ticker", "option_type", "strike", "expiry", "multiplier"]);
  assert.equal(H.manualRow({ ticker: "AAPL", quantity: "1" }).asset_kind, undefined);
});

test("write controls render only for can_write === true AND the key listed (no truthy fallback)", () => {
  const env = { permissions: { can_write: true }, vocabularies: { editable: ["add"] } };
  assert.equal(P.canDo(env, "add"), true);
  assert.equal(P.canDo(env, "revalue"), false);
  assert.equal(P.canDo({ permissions: { can_write: false }, vocabularies: { editable: ["add"] } }, "add"), false);
  assert.equal(P.canDo({ permissions: { can_write: "yes" }, vocabularies: { editable: ["add"] } }, "add"), false);
  assert.equal(P.canDo(null, "add"), false);
  assert.equal(P.canDo({ permissions: { can_write: true } }, "add"), false);
});

test("payloads keep amounts as typed strings; money groups without float arithmetic", () => {
  const a = P.directAssetPayload({ kind: "real_estate", name: " Villa ", value: "4250000.10", valuation_date: "2026-10-01",
    method: "appraisal", ownership_pct: "50", cost_basis: "" });
  assert.deepEqual(a, { kind: "real_estate", name: "Villa", valuation: { value: "4250000.10", date: "2026-10-01",
    method: "appraisal" }, ownership_pct: "50" });
  const p = P.privatePayload({ kind: "fund", name: "Fund IV", amount: "5000000", commitment_date: "2021-03-01",
    vintage_year: "2021", called_to_date: "3250000", nav: "4100000", nav_date: "2026-09-30" });
  assert.equal(p.commitment.vintage_year, 2021);
  assert.equal(p.called_to_date, "3250000");
  assert.deepEqual(p.nav, { value: "4100000", date: "2026-09-30" });
  assert.equal(P.money("12345678.5"), "$12,345,678.50");
  assert.equal(P.money("0.30000000000000004"), "$0.30");
  assert.equal(P.asOfText({ holdings_as_of: "2026-11-04" }), "Holdings as of 2026-11-04");
  assert.equal(P.asOfText(null), "Holdings not yet rolled up");
});

test("the proxy forwards ONLY the allow-listed PORTENTRY1 paths", () => {
  const id = "00000000-0000-0000-0000-000000000123";
  assert.equal(P.portentryAllowed("GET", `entities/${id}/direct-assets`), true);
  assert.equal(P.portentryAllowed("POST", `admin/integrated-imports/${id}/approve`), true);
  assert.equal(P.portentryAllowed("DELETE", `entities/${id}/direct-assets`), false);
  assert.equal(P.portentryAllowed("GET", "admin/users"), false);
  assert.equal(P.portentryAllowed("POST", `entities/${id}/../admin`), false);
  assert.deepEqual(P.importActions({ actions: [] }), []);
  assert.deepEqual(P.importActions(null), []);
});

test("detail fields: an 'entities' field (ILIT insureds) round-trips as an id list", () => {
  const f = { key: "policy_insureds", type: "entities", editable_here: true, value: ["a", "b"] };
  assert.equal(D.fieldInputValue(f), "a,b");
  assert.deepEqual(D.fieldPayload([f], { policy_insureds: "a,b,c" }), { policy_insureds: ["a", "b", "c"] });
});
