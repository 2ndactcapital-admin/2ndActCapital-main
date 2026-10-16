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
  secondReaderFor,
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

test("second-reader findings come from the response, never a default", () => {
  const note = { second_reader: { by_field: { underlyings: [{ severity: "critical", evidence: "junk entry" }] } } };
  assert.equal(secondReaderFor(note, "underlyings").length, 1);
  assert.deepEqual(secondReaderFor(note, "cap_pct"), []);
  assert.deepEqual(secondReaderFor(null, "underlyings"), []);
  assert.deepEqual(secondReaderFor({}, "underlyings"), []);
});

test("write gate fails closed", () => {
  const env = { permissions: { can_write: true }, vocabularies: { editable: ["value", "action", "notes"] } };
  assert.equal(canWriteGold(env), true);
  assert.equal(canWriteGold({}), false);
  assert.equal(canWriteGold({ ...env, permissions: { can_write: "true" } }), false);
  assert.equal(canWriteGold({ ...env, vocabularies: { editable: [] } }), false);
});

// ── bulkfetch Part B ──
import { checkIdentifier, isReviewable, listTable, matchLabel, rowQuote } from "../../lib/noteGoldReview.mjs";

const UNDERLYINGS = {
  key: "underlyings",
  kind: "list",
  members: [
    { key: "name", kind: "text", required: true },
    { key: "ticker", kind: "text", required: false },
    { key: "kind", kind: "enum", enum: ["index", "etf", "single_stock", "rate", "other"], required: false },
    { key: "initial_level", kind: "number", required: false },
  ],
};

test("list values render as one row per member, columns from the member model", () => {
  const t = listTable(UNDERLYINGS, [
    { name: "S&P 500 Index", ticker: "SPX", kind: "index", initial_level: 2643.85 },
    { name: "Russell 2000 Index", ticker: "RTY", kind: "index", initial_level: 1473.536 },
  ]);
  assert.deepEqual(t.columns, ["name", "ticker", "kind", "initial_level"]);
  assert.equal(t.rows.length, 2);
  assert.equal(t.rows[1].ticker, "RTY");
  assert.deepEqual(listTable({ key: "x", kind: "list" }, [{ a: 1 }]).columns, []);
});

test("row quote: own quote, else the field quote around the name, else null", () => {
  assert.equal(rowQuote({ name: "SPX", quote: "own" }, "field"), "own");
  assert.match(rowQuote({ name: "Russell 2000" }, "the S&P 500 Index and the Russell 2000 Index"), /Russell 2000/);
  assert.equal(rowQuote({ name: "Nikkei" }, "the S&P 500 Index"), null);
});

test("CUSIP check digit refuses 09709TJV5 and accepts 09709TJV4; ISIN mismatch is a warning", () => {
  assert.deepEqual(checkIdentifier("cusip", "09709tjv4"), { value: "09709TJV4" });
  assert.ok(checkIdentifier("cusip", "09709TJV5").error);
  assert.ok(checkIdentifier("cusip", "09709TJV").error);
  assert.deepEqual(checkIdentifier("isin", "US09709TJV44", "09709TJV4"), { value: "US09709TJV44" });
  const w = checkIdentifier("isin", "US06745QAB18", "09709TJV4");
  assert.equal(w.value, "US06745QAB18");
  assert.ok(w.warning);
  assert.ok(checkIdentifier("isin", "US09709TJV45").error);
});

test("calculated fields are not reviewable; match labels", () => {
  assert.equal(isReviewable({ key: "total_fees_usd", calculated: true }), false);
  assert.equal(isReviewable({ key: "cusip", calculated: false }), true);
  assert.equal(matchLabel({ matched: false }), "no match");
  assert.match(matchLabel({ matched: true, canonical: "Merrill Lynch", affiliate: true }), /affiliate of this note's issuer/);
  assert.equal(matchLabel(null), null);
});
