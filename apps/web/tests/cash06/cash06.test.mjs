// CASH06 — Household → Treasury → Rules: the REAL components rendered with react-dom/server from reads shaped exactly
// like routers/cash_rules.py returns them (services/cash_rules.version_out / list_rules / detail / options / preview):
// the list (status, last run, next run, holds, the server's own row actions), the editor per rule type with its preview
// (what it WOULD draft today — nothing sent), the run history (engine runs → drafted instructions, each a link into
// Payments, or the hold and why), the member's read-only view (active rules, the cash.rules text, last four only), the
// approve control only where the server lists it, and every screen FAILING CLOSED on a lost read.
import assert from "node:assert/strict";
import { register } from "node:module";
import test from "node:test";

register("../market/jsxLoader.mjs", import.meta.url);

const { createElement: h } = await import("react");
const { renderToStaticMarkup } = await import("react-dom/server");
const L = await import("../../lib/cashRules.mjs");
const R = await import("../../components/treasury/TreasuryRules.jsx");
const TreasuryRules = R.default;
const { RuleList, RuleEditor, RuleDetail, RuleHistory } = R;
const Daily = (await import("../../components/treasury/TreasuryDaily.jsx")).default;

const text = (html) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&")
  .replace(/&quot;/g, '"').replace(/&gt;/g, ">").replace(/&lt;/g, "<").replace(/\s+/g, " ");
const html = (el) => renderToStaticMarkup(el);
const render = (el) => text(html(el));
const buttons = (el) => [...html(el).matchAll(/<button[^>]*>([^<]*)<\/button>/g)].map((m) => m[1].trim());
const options = (el, label) => {
  const m = html(el).match(new RegExp(`<select[^>]*aria-label="${label}"[^>]*>([\\s\\S]*?)</select>`));
  return m ? [...m[1].matchAll(/<option value="([^"]*)"[^>]*>([^<]*)<\/option>/g)].map((x) => [x[1], x[2]]) : null;
};

const LABELS = { "cash.rules": { key: "cash.rules", text: "Rules prepare payment instructions for approval (library)." },
  "cash.payments": { key: "cash.payments", text: "ACH settles provisionally (library)." } };
const STAFF = { can_read: true, can_write: true, can_approve: true, is_super_admin: false, read_permission: "view_portfolio",
  write_permission: "initiate_payment", approve_permission: "approve_payment" };
const VOCAB = {
  rule_types: [{ key: "distribute", label: "Distribute (srv)" }, { key: "maintain_target", label: "Target (srv)" },
    { key: "recurring", label: "Recurring (srv)" }, { key: "redirect_incoming", label: "Redirect (srv)" }],
  bases: [{ key: "pct_balance", label: "% of balance (srv)" }, { key: "fixed", label: "Fixed (srv)" }, { key: "pct_incoming", label: "% incoming (srv)" }],
  conditions: [{ key: "below", label: "Below (srv)" }, { key: "above", label: "Above (srv)" }],
  frequencies: [{ key: "daily", label: "Daily (srv)" }, { key: "business_days", label: "Business days (srv)" },
    { key: "weekly", label: "Weekly (srv)" }, { key: "monthly", label: "Monthly (srv)" }, { key: "on_incoming", label: "On credit (srv)" }],
  weekdays: [{ key: 1, label: "Monday" }, { key: 5, label: "Friday" }], rails: [{ key: "ach", label: "ACH (srv)" }],
  statuses: [], outcomes: [], editable: ["name"], inline_editable: [],
};
const ACC = (n) => ({ id: `a-${n}`, account_number_masked: `****${n}` });
const VER = (over = {}) => ({ id: "v-1", rule_id: "r-1", version_no: 2, name: "Keep checking at $10k", rule_type: "distribute",
  rule_type_label: "Distribute (srv)", status: "active", status_label: "Active (srv)", stage: "A", source: ACC("6001"),
  supporting: null, destinations: [{ ...ACC("6002"), pct: "40" }, { ...ACC("6003"), pct: "12.5" }], basis: "pct_balance",
  frequency: "weekly", frequency_label: "Weekly (srv)", day_of_week: 5, day_of_month: null, rail: "ach", rail_label: "ACH (srv)",
  cap_per_firing: "50000.00", max_per_day: 2, owner_id: "u-own", owner_name: "Olive Owner", maker_ids: ["u-own"],
  makers: ["Olive Owner"], approved_by_name: "Carl Checker", actions: [], ...over });
const LIST = (over = {}) => ({ household: { id: "h-1" }, member_view: false, labels: LABELS, permissions: STAFF, vocabularies: VOCAB,
  today: "2026-11-10", actions: [{ key: "new", label: "New rule" }], items: [
    { rule_id: "r-1", current: VER({ actions: [{ key: "pause", label: "Pause (srv)" }] }),
      open_version: { id: "v-3", version_no: 3, status_label: "Awaiting approval (srv)" },
      last_run: { at: "2026-11-06T15:00:00+00:00", outcome: "drafted", outcome_label: "Drafted for approval (srv)" },
      next_run: "2026-11-13T15:00:00+00:00", holds: 1 },
    { rule_id: "r-2", current: VER({ id: "v-9", rule_id: "r-2", name: "Sweep deposits", rule_type: "redirect_incoming",
      rule_type_label: "Redirect (srv)", destinations: [{ ...ACC("6004"), pct: "25" }], frequency: "on_incoming",
      frequency_label: "On credit (srv)", day_of_week: null }), last_run: null, next_run: null,
      next_run_label: "on each incoming credit", holds: 0 }], ...over });
const OPTIONS = (over = {}) => ({ household: { id: "h-1" }, member_view: false, labels: LABELS, permissions: STAFF, vocabularies: VOCAB,
  today: "2026-11-10", max_destinations: 5, fire_time: "10:00 Eastern",
  ceilings: { max_amount_per_firing: "250000.00", max_instructions_per_day: 5 },
  sources: [
    { ...ACC("6001"), eligible: true, reasons: [], rails: [{ key: "ach", label: "ACH (srv)" }], destinations: [ACC("6002"), ACC("6003")] },
    { ...ACC("6009"), eligible: false, reasons: ["trading authority (custody): 'inquiry' (srv)", "account authority: advise (srv)"],
      rails: [], destinations: [ACC("6010")] }], ...over });
const PREVIEW = { preview: { outcome: "partly_held", outcome_label: "Drafted in part; the rest held (srv)", total: "154.32",
  value_date: "2026-11-10", drafts: [{ amount: "154.32", source: ACC("6001"), destination: ACC("6002"),
    reason: "12.5% of ****6001's balance of 1234.57" }],
  holds: [{ kind: "cap", kind_label: "Per-firing cap (srv)", reason: "411.52 to ****6003 is over the cap — held" }],
  notes: [], skipped: null, refusals: [], example_credit: null, would_send: false } };
const DETAIL = (over = {}, ver = {}) => ({ household: { id: "h-1" }, member_view: false, labels: LABELS, permissions: STAFF,
  vocabularies: VOCAB, rule: { household_id: "h-1", version: VER(ver), versions: [{ id: "v-1", version_no: 2, status_label: "Active (srv)" }],
    history: [
      { id: "f-1", period_key: "w:2026-W45", trigger_type: "scheduled", workflow_run_id: "1234567890abcdef", run_status: "completed",
        evaluated_on: "2026-11-06", outcome: "drafted", outcome_label: "Drafted for approval (srv)", reason: null, holds: [],
        instructions: [{ id: "p-1", status: "pending_approval", status_label: "Awaiting approval (srv)", amount: "4000.00", value_date: "2026-11-06" }] },
      { id: "f-2", period_key: "w:2026-W44", trigger_type: "scheduled", workflow_run_id: "abcdef1234567890", run_status: "completed",
        evaluated_on: "2026-10-30", outcome: "held", outcome_label: "Held (srv)", reason: "****6001 holds 10.00 available — no partial overdraft",
        holds: [{ kind: "shortfall" }], instructions: [] }] }, ...over });
const MEMBER = () => LIST({ member_view: true, permissions: { can_read: true, can_write: false, can_approve: false },
  actions: [{ key: "new", label: "New rule" }], vocabularies: { ...VOCAB, editable: [] },
  items: [{ rule_id: "r-1", current: VER({ actions: [{ key: "pause", label: "Pause (stale)" }] }) }] });

test("list: rules from the server (last four only), schedule, last / next run, holds, the open version, both disclosures", () => {
  const el = h(RuleList, { data: LIST() });
  const t = render(el);
  assert.ok(t.includes("Keep checking at $10k") && t.includes("····6001") && t.includes("····6002 (40%), ····6003 (12.5%)"));
  assert.ok(t.includes("Weekly (srv) (Friday)") && t.includes("2026-11-06 · Drafted for approval (srv)") && t.includes("2026-11-13 15:00 UTC"));
  assert.ok(t.includes("1 held") && t.includes("Awaiting approval (srv) (v3)") && t.includes("on each incoming credit"));
  assert.ok(!t.includes("****6001"), "never the stored mask as-is — the last four only");
  assert.ok(t.includes("Rules prepare payment instructions for approval (library).") && t.includes("ACH settles provisionally (library)."));
  assert.deepEqual(buttons(el), ["New rule"]);
  assert.deepEqual(buttons(h(RuleList, { data: LIST({ permissions: { ...STAFF, can_write: false } }) })), []);
});

test("editor per type: distribute (% of balance, many destinations), the refused sources with BOTH reasons, same-owner only", () => {
  const form = { name: "Split", rule_type: "distribute", source: "a-6001", basis: "pct_balance", frequency: "weekly",
    rail: "ach", cap_per_firing: "50000.00", destinations: [{ account_id: "a-6002", value: "40" }] };
  const el = h(RuleEditor, { options: OPTIONS(), initialForm: form });
  const t = render(el);
  assert.deepEqual(options(el, "Source account").map(([k]) => k), ["", "a-6001"]);
  assert.ok(t.includes("····6009: trading authority (custody): 'inquiry' (srv) · account authority: advise (srv)"));
  assert.deepEqual(options(el, "Destination 1").map(([k]) => k), ["", "a-6002", "a-6003"]);
  assert.deepEqual(options(el, "Basis").map(([k]) => k), ["", "pct_balance", "fixed", "pct_incoming"]);
  assert.deepEqual(options(el, "Frequency").map(([k]) => k), ["", "daily", "business_days", "weekly", "monthly"]);
  assert.ok(options(el, "Weekday") !== null && t.includes("Runs at 10:00 Eastern.") && t.includes("At most 250000.00"));
  assert.ok(html(el).includes('aria-label="Destination 1 percent"') && buttons(el).includes("Add a destination"));
  assert.deepEqual(buttons(el), ["Add a destination", "Preview today", "Save draft", "Close"]);
  assert.ok(t.includes("Rules prepare payment instructions for approval (library)."));
});

test("editor per type: maintain target (condition + target + supporting, no destinations), recurring (one $), redirect (one %, no schedule)", () => {
  const tgt = h(RuleEditor, { options: OPTIONS(), initialForm: { rule_type: "maintain_target", source: "a-6001" } });
  assert.deepEqual(options(tgt, "Condition").map(([k]) => k), ["", "below", "above"]);
  assert.deepEqual(options(tgt, "Supporting account").map(([k]) => k), ["", "a-6002", "a-6003"]);
  assert.ok(!html(tgt).includes('aria-label="Destination 1"') && html(tgt).includes('aria-label="Target balance"'));
  const rec = h(RuleEditor, { options: OPTIONS(), initialForm: { rule_type: "recurring", source: "a-6001" } });
  assert.ok(html(rec).includes('aria-label="Destination 1 amount"') && !buttons(rec).includes("Add a destination") && options(rec, "Basis") === null);
  const red = h(RuleEditor, { options: OPTIONS(), initialForm: { rule_type: "redirect_incoming", source: "a-6001" } });
  assert.ok(html(red).includes('aria-label="Destination 1 percent"') && options(red, "Frequency") === null
    && render(red).includes("Runs on each incoming credit to the source account."));
});

test("editor preview: what it WOULD draft today (amount, from / to by last four, reason), the held excess, nothing sent", () => {
  const t = render(h(RuleEditor, { options: OPTIONS(), initialForm: { rule_type: "distribute", source: "a-6001" }, preview: PREVIEW }));
  assert.ok(t.includes("If it ran today: Drafted in part; the rest held (srv) — $154.32"));
  assert.ok(t.includes("Would draft $154.32 from ····6001 to ····6002 — 12.5% of ****6001's balance of 1234.57"));
  assert.ok(t.includes("Per-firing cap (srv): 411.52 to ****6003 is over the cap — held") && t.includes("A preview drafts nothing and sends nothing."));
});

test("ruleBody: the request each type sends; refusals (third-party destination, % over 100, no declared rail) before any request", () => {
  const o = OPTIONS();
  const dist = L.ruleBody(o, { name: " Split ", rule_type: "distribute", source: "a-6001", basis: "pct_balance", frequency: "monthly",
    day_of_month: "15", rail: "ach", cap_per_firing: "50000.00", max_per_day: "2",
    destinations: [{ account_id: "a-6002", value: "40" }, { account_id: "a-6003", value: "12.5" }] });
  assert.deepEqual(dist, { ok: true, body: { name: "Split", rule_type: "distribute", source_account_id: "a-6001", rail: "ach",
    cap_per_firing: "50000.00", max_per_day: 2, basis: "pct_balance", frequency: "monthly", day_of_month: 15,
    destinations: [{ account_id: "a-6002", pct: "40" }, { account_id: "a-6003", pct: "12.5" }] } });
  const red = L.ruleBody(o, { name: "Sweep", rule_type: "redirect_incoming", source: "a-6001", rail: "ach", cap_per_firing: "900.00",
    destinations: [{ account_id: "a-6002", value: "25" }] });
  assert.equal(red.body.frequency, "on_incoming");
  assert.deepEqual(red.body.destinations, [{ account_id: "a-6002", pct: "25" }]);
  const tgt = L.ruleBody(o, { name: "Floor", rule_type: "maintain_target", source: "a-6001", frequency: "business_days", rail: "ach",
    cap_per_firing: "20000.00", condition: "below", target_amount: "10000.00", supporting: "a-6003" });
  assert.deepEqual([tgt.ok, tgt.body.supporting_account_id, tgt.body.destinations], [true, "a-6003", []]);
  const bad = L.ruleBody(o, { name: "x", rule_type: "distribute", source: "a-6001", basis: "pct_balance", frequency: "daily",
    rail: "wire", cap_per_firing: "1.00", destinations: [{ account_id: "a-6010", value: "60" }, { account_id: "a-6002", value: "50" }] });
  assert.equal(bad.ok, false);
  for (const why of ["choose a rail the custodian declares", "destinations are same-owner accounts only",
    "the percentages add up to more than 100%"]) assert.ok(bad.errors.includes(why), why);
  assert.equal(L.ruleBody(o, { rule_type: "" }).ok, false);
});

test("detail + run history: engine runs → drafted instructions linked into Payments; a hold with its reason; server actions only", () => {
  const el = h(RuleDetail, { data: DETAIL({}, { actions: [{ key: "pause", label: "Pause (srv)" }, { key: "new_version", label: "Edit (new version) (srv)" }] }), householdId: "h-1" });
  const t = render(el);
  assert.ok(t.includes("Keep checking at $10k · v2 · Active (srv)") && t.includes("Owner Olive Owner") && t.includes("approved by Carl Checker"));
  assert.ok(t.includes("$4,000.00 · Awaiting approval (srv)") && t.includes("Held (srv)") && t.includes("no partial overdraft"));
  assert.ok(html(el).includes('href="/portfolio/households/h-1?tab=treasury&amp;payment=p-1#treasury-payments"'));
  assert.ok(t.includes("12345678… (completed)"));
  assert.deepEqual(buttons(el).filter((b) => b !== "Close"), ["Pause (srv)", "Edit (new version) (srv)"]);
  // Approve only where the server lists it (a maker / the owner never sees it)
  const appr = h(RuleDetail, { data: DETAIL({}, { status: "pending_approval", actions: [{ key: "approve", label: "Approve" },
    { key: "reject", label: "Return" }] }), householdId: "h-1" });
  assert.ok(buttons(appr).includes("Approve") && html(appr).includes('aria-label="Note"'));
  assert.ok(!buttons(h(RuleDetail, { data: DETAIL({}, { status: "pending_approval", actions: [] }), householdId: "h-1" })).includes("Approve"));
  assert.equal(render(h(RuleHistory, { data: DETAIL({ rule: { version: VER(), history: [] } }), householdId: "h-1" })).includes("The rule has not run."), true);
});

test("member: their family's active rules read-only — the cash.rules text, last four, no run internals, no control at all", () => {
  const el = h(RuleList, { data: MEMBER() });
  const t = render(el);
  assert.ok(t.includes("Keep checking at $10k") && t.includes("····6002 (40%)") && t.includes("Rules prepare payment instructions for approval (library)."));
  assert.ok(!t.includes("ACH settles provisionally") && !t.includes("Last run") && !t.includes("Holds"));
  assert.deepEqual(buttons(el), []);
  assert.deepEqual(L.ruleRows(MEMBER())[0].actions, []);
  assert.equal(L.canWrite(MEMBER()), false);
  assert.equal(render(h(RuleEditor, { options: { ...OPTIONS(), member_view: true } })), "");
  assert.deepEqual(L.historyRows({ ...DETAIL(), member_view: true }, "h-1"), []);
});

test("fails closed: a lost or partial envelope renders nothing to click; the Treasury tab mounts Rules after Payments", () => {
  for (const d of [null, {}, { ...LIST(), permissions: null }, { ...LIST(), household: null },
    { ...LIST(), permissions: { can_read: "true", can_write: true } }]) {
    assert.equal(render(h(RuleList, { data: d })), "");
    assert.equal(L.newOffer(d), false);
  }
  assert.equal(render(h(RuleEditor, { options: { ...OPTIONS(), permissions: { can_read: true } } })), "");
  assert.equal(render(h(RuleDetail, { data: { ...DETAIL(), permissions: undefined } })), "");
  assert.equal(render(h(TreasuryRules, { householdId: "h-1", initial: {} })), "");
  const t = render(h(TreasuryRules, { householdId: "h-1", initial: { list: LIST() } }));
  assert.ok(t.includes("Cash rules") && t.includes("never approves, sends or moves money"));
  const daily = render(h(Daily, { env: { household: { id: "h-1" } }, initial: { rules: { list: LIST() }, payments: null } }));
  assert.ok(daily.includes("Cash rules") && daily.includes("Keep checking at $10k"));
});
