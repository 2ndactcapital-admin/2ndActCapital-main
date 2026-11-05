// CRMDETAIL1 — the Relationships / Detail fields / Liabilities helpers. The vocabulary below is written by hand in
// the API's envelope shape (services/crm_vocab.py), never imported from it.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const L = await import("../../lib/crmDetail.mjs");

const P = ["individual"];
const VOCAB = {
  relationship_types: [
    { key: "spouse", label: "Spouse", reverse_label: "Spouse", from_types: P, to_types: P, symmetric: true, group: "family", panel: true },
    { key: "parent_of", label: "Parent of", reverse_label: "Child of", from_types: P, to_types: P, symmetric: false, group: "family", panel: true },
    { key: "trustee", label: "Trustee of", reverse_label: "Trustee", from_types: ["corporation", "individual"], to_types: ["foundation", "trust"], symmetric: false, group: "trust", panel: true },
    { key: "ownership", label: "Owns", reverse_label: "Owned by", from_types: null, to_types: null, symmetric: false, group: "ownership", panel: false },
  ],
  relationship_groups: [{ key: "family", label: "Family" }, { key: "trust", label: "Trusts" }, { key: "ownership", label: "Ownership" }],
  entity_type_labels: { trust: "Trust", individual: "Individual" },
  net_worth_lines: [{ key: "gross", label: "Gross assets" }, { key: "liabilities", label: "Liabilities" }, { key: "net", label: "Net worth" }],
};

test("a lost or view-only envelope fails CLOSED (no write controls)", () => {
  assert.equal(L.canWrite(undefined), false);
  assert.equal(L.canWrite({}), false);
  assert.equal(L.canWrite({ permissions: { can_write: "true" } }), false);
  assert.equal(L.canWrite({ permissions: { can_write: false } }), false);
  assert.equal(L.canWrite({ permissions: { can_write: true } }), true);
  assert.deepEqual(L.editableList({ vocabularies: {} }), []);
  assert.deepEqual(L.editableList(null), []);
});

test("relationship options come from the vocabulary: both directions, reverse labels, never ownership", () => {
  const person = L.relationshipOptions(VOCAB, "individual");
  assert.deepEqual(person.map((o) => o.value), ["spouse|outgoing", "parent_of|outgoing", "parent_of|incoming", "trustee|outgoing"]);
  assert.equal(person.find((o) => o.value === "parent_of|incoming").label, "Child of");
  const trust = L.relationshipOptions(VOCAB, "trust");
  assert.deepEqual(trust.map((o) => [o.value, o.label]), [["trustee|incoming", "Trustee"]]);
  assert.ok(!L.relationshipOptions(VOCAB, "llc").some((o) => o.key === "ownership"));
  assert.deepEqual(L.relationshipOptions({}, "trust"), []);
  assert.deepEqual(L.parseOption("trustee|incoming"), { key: "trustee", direction: "incoming" });
  assert.equal(L.parseOption("trustee"), null);
});

test("rows group in the envelope's order with its labels", () => {
  const rows = [{ id: "1", group: "trust" }, { id: "2", group: "family" }, { id: "3", group: "zzz" }];
  const g = L.groupRows(rows, VOCAB);
  assert.deepEqual(g.map((x) => [x.label, x.rows.map((r) => r.id)]), [["Family", ["2"]], ["Trusts", ["1"]], ["Other", ["3"]]]);
});

test("detail fields: only changed, editable fields are sent; decimals stay strings; blank clears", () => {
  const fields = [
    { key: "trust_type", type: "choice", value: "dynasty", editable_here: true },
    { key: "unitrust_rate_pct", type: "decimal", value: null, editable_here: true },
    { key: "gst_exempt", type: "boolean", value: true, editable_here: true },
    { key: "state_of_formation", type: "region", value: "DE", editable_here: false },
  ];
  const form = { trust_type: "dynasty", unitrust_rate_pct: "5.25", gst_exempt: "", state_of_formation: "CT" };
  assert.deepEqual(L.fieldPayload(fields, form), { unitrust_rate_pct: "5.25", gst_exempt: null });
  assert.equal(L.fieldInputValue(fields[2]), "true");
});

test("money is formatted from the exact string, never via a float", () => {
  assert.equal(L.formatMoney("601788090.00"), "$601,788,090.00");
  assert.equal(L.formatMoney("0.1"), "$0.10");
  assert.equal(L.formatMoney("-187500.5"), "−$187,500.50");
  assert.equal(L.formatMoney(null), "—");
  const lines = L.netWorthLines({ gross: "100.00", liabilities: "40.00", net: "60.00", currency: "USD" }, VOCAB);
  assert.deepEqual(lines.map((l) => [l.label, l.text]), [["Gross assets", "$100.00"], ["Liabilities", "$40.00"], ["Net worth", "$60.00"]]);
  assert.deepEqual(L.netWorthLines(null, VOCAB), []);
});

test("liability payload keeps amounts as strings and drops blanks (create) / nulls them (edit)", () => {
  const f = { name: " Mortgage ", kind: "mortgage", current_balance: "3,500,000.00", original_amount: "", as_of: "2026-11-04" };
  assert.deepEqual(L.liabilityPayload(f), { name: "Mortgage", kind: "mortgage", current_balance: "3500000.00", as_of: "2026-11-04" });
  assert.equal(L.liabilityPayload(f, { editing: true }).original_amount, null);
});

test("person markers come from the API flags", () => {
  assert.deepEqual(L.personMarkers({ is_minor: true }), ["Minor"]);
  assert.deepEqual(L.personMarkers({ is_minor: false, is_deceased: true }), ["Deceased"]);
  assert.deepEqual(L.personMarkers({}), []);
});

test("every write control in the three panels sits behind canWrite(env) (no truthy fallback)", () => {
  for (const f of ["RelationshipsPanel.jsx", "DetailFieldsPanel.jsx", "LiabilitiesTab.jsx"]) {
    const src = readFileSync(new URL(`../../components/crm/${f}`, import.meta.url), "utf8");
    assert.ok(/const writable = canWrite\(env\)/.test(src), f);
    assert.ok(!/\|\|\s*DEFAULT/.test(src), f);
    assert.ok(!/can_write\s*\|\|/.test(src), f);
  }
});
