// goldset — the gold review screen's pure logic.
import assert from "node:assert/strict";
import test from "node:test";

import {
  highlightSegments,
  keyAction,
  listRows,
  orderedFields,
  parseList,
  parseRange,
  parseScalar,
  step,
} from "../../lib/noteGoldReview.mjs";
import { canWriteGold } from "../../lib/noteGoldGates.mjs";

const RANGE = { key: "agent_commission_pct", kind: "range", range_bounds: ["exact", "up_to", "between", "not_less_than"] };
const LIST = {
  key: "distribution",
  kind: "list",
  members: [
    { key: "name", kind: "text", required: true },
    { key: "role", kind: "enum", enum: ["dealer", "distribution_agent"], required: true },
    { key: "fee_max_pct", kind: "number", required: false },
  ],
};

test("fields follow the server's sections, critical sections first", () => {
  const payload = {
    fields: [{ key: "a" }, { key: "b" }, { key: "c" }],
    sections: [{ key: "economics", fields: ["c", "a"] }, { key: "rules_only", fields: ["b", "zzz"] }],
  };
  assert.deepEqual(orderedFields(payload).map((f) => f.key), ["c", "a", "b"]);
  assert.deepEqual(orderedFields(null), []);
});

test("keyboard: J/K/C/A/E/N map to actions; typing in an input does not", () => {
  const ev = (key, tagName = "BODY", extra = {}) => ({ key, target: { tagName }, ...extra });
  assert.equal(keyAction(ev("j")), "next");
  assert.equal(keyAction(ev("K")), "prev");
  assert.equal(keyAction(ev("c")), "confirm");
  assert.equal(keyAction(ev("a")), "absent");
  assert.equal(keyAction(ev("e")), "edit");
  assert.equal(keyAction(ev("n")), "nextNote");
  assert.equal(keyAction(ev("c", "INPUT")), null);
  assert.equal(keyAction(ev("c", "BODY", { metaKey: true })), null);
  assert.equal(keyAction(ev("x")), null);
  assert.equal(step(0, 5, "prev"), 0);
  assert.equal(step(4, 5, "next"), 4);
  assert.equal(step(2, 5, "next"), 3);
});

test("ranges: min/max, inferred bound, min > max refused", () => {
  assert.deepEqual(parseRange(RANGE, "", "2.5", "").value, { min: null, max: 2.5, bound: "up_to" });
  assert.deepEqual(parseRange(RANGE, "1", "1", "").value, { min: 1, max: 1, bound: "exact" });
  assert.ok(parseRange(RANGE, "3", "2", "").error);
  assert.ok(parseRange(RANGE, "", "", "").error);
  assert.ok(parseRange(RANGE, "1", "2", "nonsense").error);
});

test("lists: rows become members; required and enum enforced; empty refused", () => {
  const ok = parseList(LIST, [{ name: "Dealer LLC", role: "dealer", fee_max_pct: "1.5" }]);
  assert.deepEqual(ok.value, [{ name: "Dealer LLC", role: "dealer", fee_max_pct: 1.5 }]);
  assert.ok(parseList(LIST, [{ name: "", role: "dealer" }]).error);
  assert.ok(parseList(LIST, [{ name: "X", role: "boss" }]).error);
  assert.ok(parseList(LIST, []).error);
  assert.deepEqual(listRows(LIST, [{ name: "A", role: "dealer", fee_max_pct: null }]),
    [{ name: "A", role: "dealer", fee_max_pct: "" }]);
});

test("scalars parse by kind", () => {
  assert.equal(parseScalar({ kind: "number" }, "$1,000.50").value, 1000.5);
  assert.ok(parseScalar({ kind: "date" }, "3/1/2026").error);
  assert.equal(parseScalar({ kind: "enum", enum: ["buffer"] }, "buffer").value, "buffer");
  assert.ok(parseScalar({ kind: "enum", enum: ["buffer"] }, "barrier").error);
});

test("highlight splits around the quote span, and ignores a bad span", () => {
  assert.deepEqual(highlightSegments("abcdef", 2, 4).map((s) => [s.text, s.mark]),
    [["ab", false], ["cd", true], ["ef", false]]);
  assert.equal(highlightSegments("abc", null, null).length, 1);
  assert.equal(highlightSegments("abc", 2, 9).length, 1);
});

test("write gate fails closed", () => {
  const env = { permissions: { can_write: true }, vocabularies: { editable: ["value", "action", "notes"] } };
  assert.equal(canWriteGold(env), true);
  assert.equal(canWriteGold({}), false);
  assert.equal(canWriteGold({ ...env, permissions: { can_write: "true" } }), false);
  assert.equal(canWriteGold({ ...env, vocabularies: { editable: [] } }), false);
});
