// bulkfetchui — the Bulk download tab's pure logic, fed the API's real envelope shapes.
import assert from "node:assert/strict";
import test from "node:test";

import {
  buildParams,
  canWrite,
  laptopStatus,
  presetRange,
  previewReady,
  progressPercent,
  queueOutcome,
  renderStatus,
  rowActions,
  runsActive,
} from "../../lib/edgarBulkDownload.mjs";

const WRITE = { permissions: { can_read: true, can_write: true } };
const READ_ONLY = { permissions: { can_read: true, can_write: false } };

test("presets end at the last indexed date", () => {
  assert.deepEqual(presetRange("2025-2026", "2026-10-01"), { from: "2025-01-01", to: "2026-10-01" });
  assert.deepEqual(presetRange("ytd", "2026-10-01"), { from: "2026-01-01", to: "2026-10-01" });
  assert.deepEqual(presetRange("last12", "2026-10-01"), { from: "2025-10-02", to: "2026-10-01" });
  assert.equal(presetRange("custom", "2026-10-01"), null);
  assert.equal(presetRange("ytd", null), null);
});

test("params cap max requests/second at 8 and carry no org_id", () => {
  const p = buildParams({ banks: new Set(["UBS"]), from: "2025-01-01", to: "2025-12-31", kinds: new Set(["final"]),
    peek: true, concurrency: "6", maxRps: 12 });
  assert.equal(p.max_rps, 8);
  assert.equal(p.concurrency, 6);
  assert.equal("org_id" in p, false);
  assert.equal(previewReady(p), true);
  assert.equal(previewReady({ ...p, banks: [] }), false);
  assert.equal(previewReady({ ...p, kinds: [] }), false);
});

test("laptop status: online shows the host; offline shows the start command", () => {
  const on = laptopStatus({ laptop: { online: true, worker: { host: "joe-laptop" } } });
  assert.deepEqual([on.online, on.text, on.command], [true, "worker running on joe-laptop", null]);
  const off = laptopStatus({ laptop: { online: false, worker: null, start_command: "doppler run -- x",
    tmux_command: "tmux new -s bulkworker 'doppler run -- x'" } });
  assert.equal(off.online, false);
  assert.equal(off.text, "no worker — start it with");
  assert.match(off.command, /^tmux new -s bulkworker/);
});

test("render status: not set up is greyed but still a state (selectable)", () => {
  const r = renderStatus({ render: { configured: false, online: false } });
  assert.deepEqual([r.state, r.text, r.greyed], ["not_set_up", "not set up", true]);
  assert.equal(renderStatus({ render: { configured: true, online: true } }).state, "online");
});

test("row actions need BOTH the server's per-row action AND can_write — fail closed", () => {
  const run = { actions: { stop: true, cancel: false, requeue: false } };
  assert.deepEqual(rowActions(run, WRITE), ["stop"]);
  assert.deepEqual(rowActions(run, READ_ONLY), []);
  assert.deepEqual(rowActions(run, null), []);
  assert.deepEqual(rowActions(run, {}), []);
  assert.equal(canWrite({ permissions: { can_write: "true" } }), false);
});

test("progress, activity and the queue outcome", () => {
  assert.equal(progressPercent({ done: 50, total: 200 }), 25);
  assert.equal(progressPercent({ done: 0, total: 0, status: "succeeded" }), 100);
  assert.equal(runsActive({ rows: [{ status: "succeeded" }, { status: "running" }] }), true);
  assert.equal(runsActive({ rows: [{ status: "stopped" }] }), false);
  const refused = queueOutcome({ ok: false, error: "Render worker is not set up — run on Laptop, or see setup steps",
    detail: { setup_anchor: "render-worker-setup" } });
  assert.equal(refused.error, true);
  assert.equal(refused.setupAnchor, "render-worker-setup");
  const queued = queueOutcome({ ok: true, payload: { worker_online: false, start_command: "cmd" } });
  assert.deepEqual([queued.error, queued.command], [false, "cmd"]);
});
