// ROLLUP2 — the net-worth surfaces render what the API computed: the Unclassified holdings (counted, listed), the
// valueless holdings (listed, not counted), the Family / Charitable / Household split, the household's Owners list,
// the ownership tree's "via" label, the duplicate-ownership warning, and the optional cash-line Name. Envelopes are
// written by hand in the API's shapes (services/networth, services/household_total, routers/entity_graph), never
// imported from them.
import assert from "node:assert/strict";
import { register } from "node:module";
import test from "node:test";

register("../market/jsxLoader.mjs", import.meta.url);

const { createElement: h } = await import("react");
const { renderToStaticMarkup } = await import("react-dom/server");
const R = await import("../../lib/rollup2.mjs");
const B4 = await import("../../lib/bugfix4.mjs");
const P = await import("../../lib/portentry.mjs");
const { OwnersTable, SplitTable, UnclassifiedList } = await import("../../components/networth/NetWorthDetails.jsx");

const VOCAB = {
  unclassified: { key: "unclassified", label: "Unclassified" },
  split_lines: [{ key: "family", label: "Family net worth" }, { key: "charitable", label: "Charitable" },
    { key: "household", label: "Household total" }],
};
const CARD = {
  gross: "11100000.00", liabilities: "0.00", net: "11100000.00", holdings_as_of: "2026-11-04", positions_counted: 2,
  unvalued_positions: 1, unclassified_value: "11100000.00",
  unclassified: [{ position_id: "p1", label: "Schwab US Dividend Equity ETF (SCHD)", owner_name: "Olivo 2016 CRUT",
    value: "5880000.00", as_of_date: "2026-11-04" },
  { position_id: "p2", label: "Schwab Intermediate-Term U.S. Treasury ETF (SCHR)", owner_name: "Olivo 2016 CRUT",
    value: "5220000.00", as_of_date: "2026-11-04" }],
  skipped: [{ position_id: "p3", label: "Valueless line", owner_name: "Olivo 2016 CRUT", reason: "no market_value" }],
};

test("unclassified holdings are listed as counted, valueless ones as not counted", () => {
  assert.equal(R.unclassifiedRows(CARD).length, 2);
  assert.equal(R.skippedRows(CARD)[0].reason, "no market_value");
  const html = renderToStaticMarkup(h(UnclassifiedList, { netWorth: CARD, vocabularies: VOCAB }));
  assert.match(html, /Unclassified — counted in net worth/);
  assert.match(html, /SCHD/);
  assert.match(html, /1 holding\(s\) have no value and are not counted/);
  assert.equal(renderToStaticMarkup(h(UnclassifiedList, { netWorth: { unclassified: [], skipped: [] } })), "");
  assert.deepEqual(R.unclassifiedRows(null), []);
});

test("a card with holdings never says 'not yet rolled up'; an empty live card says no holdings", () => {
  assert.equal(P.asOfText(CARD), "Holdings as of 2026-11-04");
  assert.equal(P.asOfText({ positions_counted: 0, unvalued_positions: 0 }), "No holdings recorded");
  assert.equal(P.asOfText(null), "Holdings not yet rolled up");
});

const TOTAL = {
  split: {
    family: { gross: "601975590.00", liabilities: "52800000.00", net: "549175590.00", unclassified_value: "62528900.00" },
    charitable: { gross: "62150000.00", liabilities: "0.00", net: "62150000.00", unclassified_value: "51550000.00",
      entities: [{ entity_id: "f", display_name: "Olivo Family Foundation" }, { entity_id: "c", display_name: "Olivo 2016 CRUT" }] },
    household: { gross: "664125590.00", liabilities: "52800000.00", net: "611325590.00", unclassified_value: "114078900.00" },
  },
  owners: [{ entity_id: "c", display_name: "Olivo 2016 CRUT", charitable: true, gross: "11100000.00", liabilities: "0.00",
    net: "11100000.00", unclassified_value: "11100000.00" }],
};

test("the split renders family, charitable and household in the envelope's order, with the charitable entities", () => {
  const rows = R.splitRows(TOTAL, VOCAB);
  assert.deepEqual(rows.map((r) => r.key), ["family", "charitable", "household"]);
  assert.deepEqual(rows[1].entities, ["Olivo Family Foundation", "Olivo 2016 CRUT"]);
  const html = renderToStaticMarkup(h(SplitTable, { total: TOTAL, vocabularies: VOCAB }));
  assert.match(html, /Family net worth/);
  assert.match(html, /Charitable/);
  assert.match(html, /Household total/);
  assert.equal(R.splitRows({}, VOCAB), null);
});

test("the Owners list shows every member, the CRUT included, from the server's own figures", () => {
  const html = renderToStaticMarkup(h(OwnersTable, { total: TOTAL, vocabularies: VOCAB }));
  assert.match(html, /Olivo 2016 CRUT/);
  assert.match(html, /charitable/);
  assert.equal(R.ownerRows({}).length, 0);
});

test("a nested ownership entry says whose stake it is", () => {
  assert.equal(R.treePctLabel(15, null, "down"), "15.00%");
  assert.equal(R.treePctLabel(60, "Olivo Holdings LP", "down"), "60.00% via Olivo Holdings LP");
  assert.equal(R.treePctLabel(80, "OH GP LLC", "up"), "80.00% of OH GP LLC");
  assert.equal(R.treePctLabel(null, "x"), "");
});

test("the duplicate warning renders only what the server sent", () => {
  assert.deepEqual(R.duplicateWarnings({}), []);
  const w = R.duplicateWarnings({ duplicates: [{ parent_id: "a", child_id: "b", warning: "A has 2 live ownership entries in B" }] });
  assert.equal(w.length, 1);
  assert.match(w[0].text, /2 live ownership entries/);
});

test("a cash line takes an optional Name; money market too", () => {
  const V = { value_only_kinds: ["cash", "private"], no_master_kinds: ["digital_asset", "cash"],
    name_optional_kinds: ["cash", "money_market"] };
  assert.ok(B4.lineFields("cash", V).includes("name"));
  assert.equal(B4.lineReady({ asset_kind: "cash", market_value: "1150000" }, V), true);
  assert.equal(B4.lineReady({ asset_kind: "cash", name: "Coastal Bank checking", market_value: "1150000" }, V), true);
  assert.equal(B4.lineReady({ asset_kind: "money_market", quantity: "3050000" }, V), true);
  assert.equal(B4.lineReady({ asset_kind: "mutual_fund", quantity: "1" }, V), false);
  assert.deepEqual(B4.lineRow({ asset_kind: "cash", name: "Cash / sweep", market_value: "1050000", cusip: "46090E103" }, V),
    { asset_kind: "cash", name: "Cash / sweep", market_value: "1050000" });
});
