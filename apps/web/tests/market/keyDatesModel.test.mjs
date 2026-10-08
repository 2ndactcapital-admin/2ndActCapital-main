/**
 * lib/market/keyDatesModel.mjs (mkt04c Task 2): known answers for the
 * dropdown labels (day, month precision, ranges), the server's order kept,
 * personal dates in their own list, the period a date lands on (clamped), the
 * Start/End selection and the anchor flag text.
 */
import assert from "node:assert/strict";
import { test } from "node:test";

import { interpretKeyDates } from "../../lib/market/chartRequest.mjs";
import { snapAnchorIndex } from "../../lib/market/chartModel.mjs";
import {
  EDGE_END,
  EDGE_START,
  SOURCE_KEY,
  SOURCE_MY,
  anchorDateFor,
  anchorFlagText,
  customDateOptions,
  formatKeyDate,
  keyDateOptions,
  periodIndexFor,
  pickDate,
  resolveDateSelection,
  selectedBandRange,
  setEdge,
} from "../../lib/market/keyDatesModel.mjs";
import { keyDates04cBody, monthEnds } from "./mkt04cFixtures.mjs";

const KD = interpretKeyDates({ ok: true, body: keyDates04cBody() });

test("date labels: day precision 'Mon D, YYYY', month precision 'Mon YYYY', junk empty", () => {
  assert.equal(formatKeyDate("2020-03-16", "day"), "Mar 16, 2020");
  assert.equal(formatKeyDate("2008-09-05", "day"), "Sep 5, 2008", "no leading zero on the day");
  assert.equal(formatKeyDate("2020-02-01", "month"), "Feb 2020", "a month-precision date is stored as the 1st and shown as its month");
  assert.equal(formatKeyDate("2020-02-01", null), "Feb 1, 2020", "no precision means the exact day");
  assert.equal(formatKeyDate("2020-2-1", "day"), "");
  assert.equal(formatKeyDate(null, "day"), "");
});

test("key-date options: server order kept, single dates and both kinds of range labelled", () => {
  const opts = keyDateOptions(KD.keyDates);
  assert.deepEqual(
    opts.map((o) => o.value),
    ["kd-old", "kd-month-range", "kd-one", "kd-day-range"],
    "chronological as returned — nothing re-sorted",
  );
  assert.deepEqual(opts.map((o) => o.label), [
    "Oct 19, 1987 · Key date before the data",
    "Feb 2020 – Apr 2020 · Month range kd",
    "Mar 16, 2020 · Key date one",
    "Mar 16, 2022 – Jul 26, 2023 · Day range kd",
  ]);
  assert.deepEqual(opts.map((o) => o.isRange), [false, true, false, true]);
  // The server's reverse order stays reversed: the model never sorts.
  assert.deepEqual(keyDateOptions([...KD.keyDates].reverse()).map((o) => o.value), ["kd-day-range", "kd-one", "kd-month-range", "kd-old"]);
});

test("personal dates are their own list, never mixed with the reference dates", () => {
  const mine = customDateOptions(KD.customDates);
  assert.deepEqual(mine, [
    { value: "11111111-0000-0000-0000-000000000001", label: "Jan 5, 2021 · Personal alpha" },
    { value: "11111111-0000-0000-0000-000000000002", label: "Jul 9, 2024 · Personal beta" },
  ]);
  const keys = keyDateOptions(KD.keyDates).map((o) => o.value);
  assert.ok(mine.every((o) => !keys.includes(o.value)));
  assert.deepEqual(customDateOptions(undefined), []);
});

test("a date maps to the period CONTAINING it, clamped at both ends", () => {
  const periods = monthEnds(2019, 1, 24); // 2019-01-31 .. 2020-12-31
  assert.equal(periodIndexFor(periods, "2020-03-16"), 14, "Mar 16 lands on Mar 2020 (2020-03-31), its own month");
  assert.equal(periods[periodIndexFor(periods, "2020-03-16")].date, "2020-03-31");
  assert.equal(periodIndexFor(periods, "2020-03-31"), 14, "a period end is its own period");
  assert.equal(periodIndexFor(periods, "2020-04-01"), 15);
  assert.equal(periodIndexFor(periods, "1987-10-19"), 0, "before the data clamps to the first period");
  assert.equal(periodIndexFor(periods, "2031-01-01"), 23, "after the data clamps to the last period");
  assert.equal(periodIndexFor([], "2020-01-01"), -1);
  assert.equal(anchorDateFor(periods, "2020-02-01"), "2020-02-29", "a month-precision date lands on its month");
  assert.equal(anchorDateFor([], "2020-02-01"), "2020-02-01");
  // Why this rule and not the chart's snap: the chart snaps on-or-before, so
  // the raw date would land on the PREVIOUS month; the period date it moves to
  // is then kept exactly by that snap.
  assert.equal(periods[snapAnchorIndex(periods, "2020-03-16")].date, "2020-02-29");
  assert.equal(snapAnchorIndex(periods, anchorDateFor(periods, "2020-03-16")), 14);
});

test("Start/End: a new pick starts at Start; End is only meaningful for a range", () => {
  const sel = pickDate(SOURCE_KEY, "kd-day-range");
  assert.deepEqual(sel, { source: SOURCE_KEY, id: "kd-day-range", edge: EDGE_START });
  const atEnd = setEdge(sel, EDGE_END);
  const r = resolveDateSelection(atEnd, KD);
  assert.equal(r.isRange, true);
  assert.equal(r.edge, EDGE_END);
  assert.equal(r.target, "2023-07-26");
  assert.deepEqual(r.band, { start: "2022-03-16", end: "2023-07-26" });
  // Picking again resets to Start.
  assert.equal(resolveDateSelection(pickDate(SOURCE_KEY, "kd-month-range"), KD).edge, EDGE_START);
  // A single date ignores End.
  const single = resolveDateSelection(setEdge(pickDate(SOURCE_KEY, "kd-one"), EDGE_END), KD);
  assert.equal(single.edge, EDGE_START);
  assert.equal(single.target, "2020-03-16");
  assert.equal(single.band, null);
  // A personal date.
  const mine = resolveDateSelection(pickDate(SOURCE_MY, "11111111-0000-0000-0000-000000000002"), KD);
  assert.equal(mine.name, "Personal beta");
  assert.equal(mine.target, "2024-07-09");
  // "" clears; an id that no longer exists resolves to nothing.
  assert.equal(pickDate(SOURCE_KEY, ""), null);
  assert.equal(resolveDateSelection(pickDate(SOURCE_KEY, "gone"), KD), null);
  assert.equal(resolveDateSelection(sel, { kind: "error", message: "x" }), null, "no envelope, nothing resolves");
});

test("the anchor flag: date only, or 'Anchor · Mon YYYY · Name' while the picked date is the anchor, '(end)' for an end anchor", () => {
  const periods = monthEnds(2019, 1, 60);
  const start = resolveDateSelection(pickDate(SOURCE_KEY, "kd-day-range"), KD);
  const iStart = periodIndexFor(periods, start.target);
  assert.equal(anchorFlagText(periods[iStart].date, iStart, periods, null), "Anchor · Mar 2022");
  assert.equal(anchorFlagText(periods[iStart].date, iStart, periods, start), "Anchor · Mar 2022 · Day range kd");
  const end = resolveDateSelection(setEdge(pickDate(SOURCE_KEY, "kd-day-range"), EDGE_END), KD);
  const iEnd = periodIndexFor(periods, end.target);
  assert.equal(anchorFlagText(periods[iEnd].date, iEnd, periods, end), "Anchor · Jul 2023 · Day range kd (end)");
  // Once the anchor moves off the date, the flag shows only the date.
  assert.equal(anchorFlagText(periods[iEnd - 1].date, iEnd - 1, periods, end), "Anchor · Jun 2023");
  // Month precision.
  const m = resolveDateSelection(pickDate(SOURCE_KEY, "kd-month-range"), KD);
  const iM = periodIndexFor(periods, m.target);
  assert.equal(anchorFlagText(periods[iM].date, iM, periods, m), "Anchor · Feb 2020 · Month range kd");
});

test("the selected-period band: the whole range for a range entry, whichever end is anchored; none for a single date", () => {
  assert.deepEqual(selectedBandRange(resolveDateSelection(pickDate(SOURCE_KEY, "kd-month-range"), KD)), { start: "2020-02-01", end: "2020-04-01" });
  assert.deepEqual(
    selectedBandRange(resolveDateSelection(setEdge(pickDate(SOURCE_KEY, "kd-month-range"), EDGE_END), KD)),
    { start: "2020-02-01", end: "2020-04-01" },
  );
  assert.equal(selectedBandRange(resolveDateSelection(pickDate(SOURCE_KEY, "kd-one"), KD)), null);
  assert.equal(selectedBandRange(null), null);
});
