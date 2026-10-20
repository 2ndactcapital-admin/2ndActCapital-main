// bulkfetchui2 — the "Run on your laptop" dialog: its decision logic, and the
// REAL LaptopWorkerDialog rendered with react-dom/server from the API's shapes.
// The command in these fixtures is a sentinel, so a rendered command can only
// have come from the envelope, never from the component.
import assert from "node:assert/strict";
import { register } from "node:module";
import test from "node:test";

register("../market/jsxLoader.mjs", import.meta.url);

const { createElement: h } = await import("react");
const { renderToStaticMarkup } = await import("react-dom/server");
const { default: LaptopWorkerDialog } = await import("../../components/admin/LaptopWorkerDialog.jsx");
const {
  LAPTOP_DIALOG_PREF_KEY,
  dialogAfterQueue,
  laptopDialogStatus,
  laptopDialogSteps,
  queueAndOpen,
  readHideWhenOnline,
  stepHasCommand,
  stepsDialog,
  writeHideWhenOnline,
} = await import("../../lib/edgarBulkDownload.mjs");

const CMD = "SENTINEL-START-COMMAND --from-the-server";
const STEPS = [
  { n: 1, key: "open_terminal", section: "start", title: "Open the Ubuntu (WSL) terminal", detail: "d1" },
  { n: 2, key: "tmux_new", section: "start", title: "Start a tmux session", detail: "d2", command: "tmux new -s bulkworker" },
  { n: 3, key: "start_worker", section: "start", title: "Start the worker", detail: "d3", command: CMD },
  { n: 4, key: "detach", section: "start", title: "Detach and leave it running", detail: "d4", keys: "Ctrl-B, then D" },
  { n: 5, key: "keep_awake", section: "ongoing", title: "Keep the laptop awake", detail: "d5" },
  { n: 6, key: "check", section: "ongoing", title: "To check on it later", detail: "d6", command: "tmux attach -t bulkworker" },
  { n: 7, key: "stop", section: "ongoing", title: "To stop the worker", detail: "d7", keys: "Ctrl-C" },
  { n: 8, key: "requeue", section: "ongoing", title: "If a run stops or the worker crashes", detail: "d8" },
];
const N_COMMANDS = STEPS.filter((s) => s.command).length;
const OFFLINE = { laptop: { online: false, worker: null }, laptop_start_command: CMD, laptop_instructions: STEPS };
const ONLINE = {
  laptop: { online: true, worker: { host: "joe-laptop" } }, laptop_start_command: CMD, laptop_instructions: STEPS,
};
const QUEUED_OFF = { ok: true, payload: { run: { id: "r-1" }, worker_online: false, laptop_start_command: CMD } };
const QUEUED_ON = { ok: true, payload: { run: { id: "r-2" }, worker_online: true, worker_host: "joe-laptop" } };
const REFUSED = { ok: false, error: "Render worker is not set up — run on Laptop, or see setup steps",
  detail: { setup_anchor: "render-worker-setup" } };

function render(dialog, workers, extra = {}) {
  return renderToStaticMarkup(h(LaptopWorkerDialog, {
    dialog, workers, checking: false, hideWhenOnline: false,
    onCheck() {}, onViewRun() {}, onClose() {}, onHideChange() {}, ...extra,
  }));
}

const count = (html, needle) => html.split(needle).length - 1;

function memoryStorage() {
  const m = new Map();
  return { getItem: (k) => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)),
    removeItem: (k) => m.delete(k), m };
}

test("the dialog opens after a Laptop queue (offline and online), carrying the run id", () => {
  assert.deepEqual(dialogAfterQueue(QUEUED_OFF, "laptop", false), { source: "queue", runId: "r-1" });
  assert.deepEqual(dialogAfterQueue(QUEUED_ON, "laptop", false), { source: "queue", runId: "r-2" });
});

test("no dialog for Render (refused or accepted), nor for a failed queue", () => {
  assert.equal(dialogAfterQueue(REFUSED, "render", false), null);
  assert.equal(dialogAfterQueue(QUEUED_ON, "render", false), null);
  assert.equal(dialogAfterQueue({ ok: false, error: "x" }, "laptop", false), null);
  assert.equal(dialogAfterQueue(undefined, "laptop", false), null);
});

test("don't-show suppresses the dialog ONLY when the worker is online", () => {
  assert.equal(dialogAfterQueue(QUEUED_ON, "laptop", true), null);
  assert.deepEqual(dialogAfterQueue(QUEUED_OFF, "laptop", true), { source: "queue", runId: "r-1" });
  // a missing worker_online is not "online": the dialog still shows
  assert.notEqual(dialogAfterQueue({ ok: true, payload: { run: { id: "r-3" } } }, "laptop", true), null);
});

test("queueAndOpen POSTs exactly once; 'Show these steps' opens with no POST and no run", async () => {
  const calls = [];
  const queueRun = async (body) => {
    calls.push(body);
    return QUEUED_OFF;
  };
  const r = await queueAndOpen({ queueRun, params: { banks: ["UBS"] }, target: "laptop", hideWhenOnline: false });
  assert.equal(calls.length, 1);
  assert.deepEqual(calls[0], { params: { banks: ["UBS"] }, target: "laptop" });
  assert.deepEqual(r.dialog, { source: "queue", runId: "r-1" });
  assert.equal(r.outcome.error, false);

  const before = calls.length;
  const steps = stepsDialog();
  assert.equal(calls.length, before);
  assert.deepEqual(steps, { source: "steps", runId: null });

  const rr = await queueAndOpen({ queueRun: async () => REFUSED, params: {}, target: "render", hideWhenOnline: false });
  assert.equal(rr.dialog, null);
  assert.equal(rr.outcome.error, true);
  assert.equal(rr.outcome.setupAnchor, "render-worker-setup");
});

test("status: amber when offline, green with the host when online", () => {
  const off = laptopDialogStatus(OFFLINE);
  assert.deepEqual([off.online, off.tone], [false, "warning"]);
  assert.equal(off.text, "No laptop worker running — your run is queued and will start when you start one");
  const on = laptopDialogStatus(ONLINE);
  assert.deepEqual([on.online, on.tone, on.host], [true, "success", "joe-laptop"]);
  assert.equal(on.text, "Laptop worker running on joe-laptop — your run will start automatically");
  assert.equal(laptopDialogStatus(null).online, false);    // no envelope: never claims a worker is running
  assert.match(laptopDialogStatus(OFFLINE, { queued: false }).text, /^No laptop worker running/);

  const htmlOff = render({ source: "queue", runId: "r-1" }, OFFLINE);
  assert.ok(htmlOff.includes("No laptop worker running — your run is queued"));
  assert.ok(htmlOff.includes("#FBF5E6"));
  assert.ok(!htmlOff.includes("Laptop worker running on"));
  const htmlOn = render({ source: "queue", runId: "r-2" }, ONLINE);
  assert.ok(htmlOn.includes("Laptop worker running on joe-laptop — your run will start automatically"));
  assert.ok(htmlOn.includes("#EEF5F1"));
});

test("start steps collapse ONLY when the worker is online; the others stay visible", () => {
  const off = laptopDialogSteps(OFFLINE, false);
  assert.deepEqual(off.visible.map((s) => s.n), [1, 2, 3, 4, 5, 6, 7, 8]);
  assert.deepEqual(off.collapsed, []);
  const on = laptopDialogSteps(ONLINE, true);
  assert.deepEqual(on.collapsed.map((s) => s.key), ["open_terminal", "tmux_new", "start_worker", "detach"]);
  assert.deepEqual(on.visible.map((s) => s.key), ["keep_awake", "check", "stop", "requeue"]);

  const htmlOff = render({ source: "steps", runId: null }, OFFLINE);
  assert.ok(!htmlOff.includes("How to start the worker"));
  assert.ok(!htmlOff.includes("<details"));
  const htmlOn = render({ source: "steps", runId: null }, ONLINE);
  const details = htmlOn.slice(htmlOn.indexOf("<details"), htmlOn.indexOf("</details>"));
  assert.ok(details.includes("How to start the worker"));
  assert.ok(details.includes(CMD) && details.includes("Start a tmux session"));
  const after = htmlOn.slice(htmlOn.indexOf("</details>"));
  for (const t of ["Keep the laptop awake", "To check on it later", "To stop the worker"]) assert.ok(after.includes(t), t);
});

test("every step with a command renders a monospace box and a Copy button; the command is the server's", () => {
  assert.equal(STEPS.filter(stepHasCommand).length, N_COMMANDS);
  assert.equal(stepHasCommand({ command: "  " }), false);
  for (const workers of [OFFLINE, ONLINE]) {
    const html = render({ source: "queue", runId: "r-1" }, workers);
    assert.equal(count(html, ">Copy</button>"), N_COMMANDS);
    assert.equal(count(html, "<pre"), N_COMMANDS);
    assert.ok(html.includes(CMD));
    assert.ok(html.includes("<kbd") && html.includes("Ctrl-B, then D"));
  }
  // no envelope -> no steps and no command at all (nothing hard-coded to fall back to)
  const empty = render({ source: "steps", runId: null }, null);
  assert.equal(count(empty, ">Copy</button>"), 0);
  assert.ok(!empty.includes("doppler"));
});

test("buttons: check again, View run only when a run was queued, Close; accessible dialog", () => {
  const q = render({ source: "queue", runId: "r-1" }, OFFLINE);
  assert.ok(q.includes("I&#x27;ve started it — check again"));
  assert.ok(q.includes(">View run</button>") && q.includes(">Close</button>"));
  assert.ok(q.includes('role="dialog"') && q.includes('aria-modal="true"') && q.includes('aria-labelledby="laptop-dialog-title"'));
  assert.ok(q.includes(">Run on your laptop</h3>"));
  assert.ok(q.includes("Don&#x27;t show this again when the worker is running"));
  const s = render({ source: "steps", runId: null }, OFFLINE);
  assert.ok(!s.includes(">View run</button>"));
  assert.ok(render({ source: "queue", runId: "r-1" }, OFFLINE, { checking: true }).includes("Checking…"));
});

test("the don't-show preference is per browser and survives a throwing storage", () => {
  const st = memoryStorage();
  assert.equal(readHideWhenOnline(st), false);
  assert.equal(writeHideWhenOnline(st, true), true);
  assert.equal(st.m.get(LAPTOP_DIALOG_PREF_KEY), "1");
  assert.equal(readHideWhenOnline(st), true);
  writeHideWhenOnline(st, false);
  assert.equal(readHideWhenOnline(st), false);
  const boom = { getItem() { throw new Error("denied"); }, setItem() { throw new Error("denied"); },
    removeItem() { throw new Error("denied"); } };
  assert.equal(readHideWhenOnline(boom), false);
  assert.equal(writeHideWhenOnline(boom, true), false);
  assert.equal(readHideWhenOnline(null), false);
});
