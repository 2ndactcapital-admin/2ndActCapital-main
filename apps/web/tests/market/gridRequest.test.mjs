/** Grid request building, debounce and newest-wins (mkt04a Task 5b). */
import assert from "node:assert/strict";
import { test } from "node:test";

import { interpretCatalog } from "../../lib/market/catalogModel.mjs";
import {
  GRID_BODY_FIELDS,
  buildGridBody,
  createDebouncer,
  createLatestGate,
  initialControls,
  startOfData,
  yearsAgo,
} from "../../lib/market/gridRequest.mjs";
import { resolveSelection } from "../../lib/market/selection.mjs";
import { catalogBody } from "./fixtures.mjs";

const catalog = interpretCatalog({ ok: true, body: catalogBody() }).catalog;
const TODAY = "2026-10-07";

test("the body holds exactly the fields POST /market/grid accepts, and nothing else", () => {
  const controls = initialControls(catalog, TODAY);
  const sel = [
    { kind: "indicator", key: "t.cat_beta.1" },
    { kind: "security", key: "aaaaaaaa-0000-0000-0000-000000000001" },
  ];
  const body = buildGridBody(sel, catalog, controls);
  assert.deepEqual(Object.keys(body).sort(), ["anchor", "frequency", "keys", "mode"]);
  assert.deepEqual(body.keys, ["t.cat_beta.1", "t.cat_alpha.0"]);
  const withEnd = buildGridBody(sel, catalog, { ...controls, end: "2025-12-31" });
  assert.deepEqual(Object.keys(withEnd).sort(), ["anchor", "end", "frequency", "keys", "mode"]);
  for (const k of Object.keys(withEnd)) assert.ok(GRID_BODY_FIELDS.includes(k));
  assert.ok(!("org_id" in withEnd) && !("user_id" in withEnd));
});

test("an empty selection builds no request", () => {
  assert.equal(buildGridBody([], catalog, initialControls(catalog, TODAY)), null);
  // Nor does a selection the catalog does not allow.
  assert.equal(buildGridBody([{ kind: "security", key: "bbbbbbbb-0000-0000-0000-000000000001" }], catalog, initialControls(catalog, TODAY)), null);
});

test("an indicator and the security priced by it ask for the series once", () => {
  const sel = [
    { kind: "indicator", key: "t.cat_alpha.0" },
    { kind: "security", key: "aaaaaaaa-0000-0000-0000-000000000001" },
  ];
  assert.deepEqual(buildGridBody(sel, catalog, initialControls(catalog, TODAY)).keys, ["t.cat_alpha.0"]);
});

test("defaults: mode 'default', frequency 'monthly', anchor five years ago — taken from the server's vocabulary", () => {
  assert.deepEqual(initialControls(catalog, TODAY), { mode: "default", frequency: "monthly", anchor: "2021-10-07", end: "" });
  // If the server stops offering a default, the page falls back to the server's first option.
  const body = catalogBody();
  body.vocabularies.modes = [{ key: "level", label: "L" }];
  body.vocabularies.grid_frequencies = [{ key: "quarterly", label: "Q" }];
  const c = initialControls(interpretCatalog({ ok: true, body }).catalog, TODAY);
  assert.equal(c.mode, "level");
  assert.equal(c.frequency, "quarterly");
});

test("date helpers: five years ago handles 29 February; start of data is the earliest first observation", () => {
  assert.equal(yearsAgo("2028-02-29", 5), "2023-02-28");
  assert.equal(yearsAgo("2028-02-29", 4), "2024-02-29");
  const r = resolveSelection([
    { kind: "indicator", key: "t.cat_gamma.3" },
    { kind: "indicator", key: "t.cat_alpha.7" },
  ], catalog);
  assert.equal(startOfData(r), "1957-01-01");
  assert.equal(startOfData([]), null);
});

function fakeTimers() {
  let now = 0;
  let id = 0;
  const pending = new Map();
  return {
    setTimeout(fn, ms) {
      id += 1;
      pending.set(id, { fn, at: now + ms });
      return id;
    },
    clearTimeout(h) {
      pending.delete(h);
    },
    advance(ms) {
      now += ms;
      for (const [h, t] of [...pending]) {
        if (t.at <= now) {
          pending.delete(h);
          t.fn();
        }
      }
    },
  };
}

test("debounce: a rapid burst of clicks sends ONE request, with the last state", () => {
  const timers = fakeTimers();
  const sent = [];
  const d = createDebouncer((body) => sent.push(body), 300, timers);
  for (let i = 1; i <= 6; i += 1) {
    d.call({ n: i });
    timers.advance(50);
  }
  assert.equal(sent.length, 0);
  timers.advance(300);
  assert.deepEqual(sent, [{ n: 6 }]);
  // cancel() drops a pending request (an emptied selection).
  d.call({ n: 7 });
  d.cancel();
  timers.advance(1000);
  assert.equal(sent.length, 1);
});

test("newest wins: an older response arriving after a newer one is discarded", async () => {
  const gate = createLatestGate();
  const rendered = [];
  const request = async (label, delay) => {
    const token = gate.next();
    await new Promise((r) => setTimeout(r, delay));
    if (gate.isLatest(token)) rendered.push(label);
  };
  await Promise.all([request("older", 30), request("newer", 5)]);
  assert.deepEqual(rendered, ["newer"]);
  // Taking a token with no request (selection emptied) orphans one in flight.
  const t = gate.next();
  gate.next();
  assert.equal(gate.isLatest(t), false);
});
