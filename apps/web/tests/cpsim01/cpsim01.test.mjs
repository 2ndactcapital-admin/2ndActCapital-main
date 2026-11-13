// CPSIM01 — Admin → Counterparties: the REAL components rendered with react-dom/server from envelopes shaped exactly like
// routers/admin_counterparties.py returns them. Write controls FAIL CLOSED (can_write must be exactly true); the
// SIMULATED banner is on every state; tabs follow the institution type's field groups; Import / Export is disabled.
import assert from "node:assert/strict";
import { register } from "node:module";
import test from "node:test";

register("../market/jsxLoader.mjs", import.meta.url);

const { createElement: h } = await import("react");
const { renderToStaticMarkup } = await import("react-dom/server");
const C = await import("../../components/counterparties/Counterparties.jsx");
const L = await import("../../lib/counterparties.mjs");
const { MENU_ITEMS, GATE_SUPER_ADMIN, canAccess, labelFor } = await import("../../lib/menuVisibility.mjs");

const text = (html) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&").replace(/\s+/g, " ");
const render = (el) => renderToStaticMarkup(el);
const SU = { can_read: true, can_write: true, is_super_admin: true, read_permission: "super_admin", write_permission: "super_admin" };
const VIEW = { can_read: true, can_write: false, is_super_admin: false };
const TYPES = [
  { key: "altruist", label: "Altruist-compatible custodian", field_groups: ["connection", "behaviour", "clock"], description: "REST + OAuth" },
  { key: "schwab", label: "Schwab-compatible custodian", field_groups: ["connection", "fix", "behaviour", "clock"], description: "FIX" },
  { key: "domestic_bank", label: "Domestic bank", field_groups: ["connection", "bank_domestic", "behaviour", "clock"], description: "wire + ACH" },
  { key: "international_bank", label: "International bank", field_groups: ["connection", "bank_international", "behaviour", "clock"], description: "SWIFT" },
];
const VOCAB = {
  institution_types: TYPES,
  field_groups: [{ key: "connection", label: "Connection" }, { key: "fix", label: "FIX session" },
    { key: "bank_domestic", label: "Bank details (domestic)" }, { key: "bank_international", label: "Bank details (international)" },
    { key: "behaviour", label: "Behaviour" }, { key: "clock", label: "Clock" }],
  statuses: [{ key: "draft", label: "Draft" }, { key: "active", label: "Active" }, { key: "disabled", label: "Disabled" }],
  outcomes: [{ key: "accept", label: "Accept" }, { key: "reject", label: "Reject with reason" }],
  begin_strings: ["FIX.4.2", "FIX.4.4", "FIXT.1.1"],
  tabs: [{ key: "connection", label: "Connection" }, { key: "fix", label: "FIX" }, { key: "bank", label: "Bank details" },
    { key: "behaviour", label: "Behaviour" }, { key: "clock", label: "Clock" }, { key: "activity", label: "Activity" },
    { key: "import_export", label: "Import / Export" }],
  editable: ["name"], inline_editable: [],
};
const VIEW_VOCAB = { ...VOCAB, outcomes: [], begin_strings: [], editable: [] };
const ROWS = [{ id: "c1", slug: "harbor-custody", name: "Harbor Custody", institution_type: "altruist",
  institution_type_label: "Altruist-compatible custodian", status: "active", key_last4: "9f3a", business_date: "2026-11-11",
  last_request_at: "2026-11-10T15:00:00Z" }];

function detail(type, perms = SU, vocab = VOCAB) {
  return {
    simulated: true, permissions: perms, vocabularies: vocab,
    counterparty: { id: "c1", slug: "harbor", name: "Harbor", institution_type: type, status: "draft", destination_path: "/cpsim/c/harbor" },
    institution_type: TYPES.find((t) => t.key === type),
    destination_url: "https://api.example.test/cpsim/c/harbor",
    credentials: [{ id: "k1", kind: "api_key", last4: "9f3a", state: "live" }],
    fix_session: type === "schwab" ? { id: "f1", begin_string: "FIX.4.4", sender_comp_id: "HOLLISWORKS", target_comp_id: "HARBOR",
      host: "fix.harbor.cpsim.invalid", port: 9878, heartbt_int: 30, reset_on_logon: true, username: null, password_last4: "77aa" } : null,
    bank_details: type.endsWith("bank") ? [{ id: "b1", kind: type === "domestic_bank" ? "domestic" : "international",
      bank_name: "CPSIM Harbor", aba_wire: type === "domestic_bank" ? "991234560" : null, aba_ach: null,
      account_number: "CPSIM0000000001", bic: type === "international_bank" ? "CPSMXXA0XXX" : null,
      iban: type === "international_bank" ? "GB00CPSM00000000000001" : null, beneficiary_name: "Harbor (simulated)",
      beneficiary_address: "1 Simulation Way" }] : [],
    behaviour: { default_outcome: "accept", webhooks_enabled: false },
    clock: [{ business_date: "2026-11-11", reason: "initial" }],
    history: [],
  };
}

test("menu: Counterparties is a Super Admin tab (NAV1: Organization admin → Custody & Billing)", () => {
  const t = MENU_ITEMS.find((x) => x.href === "/admin/counterparties");
  // NAV1 (Joe, 2026-11-12): moved from Platform → Controls to Organization admin → Custody & Billing; gate unchanged.
  assert.ok(t && t.section === "org_admin" && t.group === "billing" && t.gate === GATE_SUPER_ADMIN, "tab present, Super Admin gate");
  assert.equal(labelFor("/admin/counterparties"), "Counterparties");
  assert.equal(canAccess({ role: "member", permissions: ["manage_org_settings"] }, t.gate), false);
});

test("banner: SIMULATED on the list page in every state (rows, empty, lost envelope)", () => {
  for (const env of [{ rows: ROWS, permissions: SU, vocabularies: VOCAB }, { rows: [] }, null]) {
    const html = render(h(C.default, { initialList: env, initialOrgAccess: null }));
    assert.ok(html.includes('data-testid="simulated-banner"') && text(html).includes("SIMULATED"), JSON.stringify(env)?.slice(0, 40));
  }
  assert.ok(text(render(h(C.SimulatedBanner))).includes("can never be real"));
});

test("list: rows with the server's labels; key shown by last four only", () => {
  const t = text(render(h(C.CounterpartyList, { envelope: { rows: ROWS, permissions: SU, vocabularies: VOCAB } })));
  for (const w of ["Harbor Custody", "Altruist-compatible custodian", "Active", "…9f3a", "2026-11-11"]) assert.ok(t.includes(w), w);
  assert.ok(text(render(h(C.CounterpartyList, { envelope: { rows: [] } }))).includes("No simulated counterparties"));
});

test("wizard: one per institution type, each showing exactly the field groups the type declares", () => {
  for (const ty of TYPES) {
    const t = text(render(h(C.CreateWizard, { envelope: { rows: [], permissions: SU, vocabularies: VOCAB }, initialType: ty.key })));
    assert.ok(t.includes("New simulated counterparty") && t.includes(ty.description), ty.key);
    for (const g of VOCAB.field_groups) {
      assert.equal(t.includes(g.label), ty.field_groups.includes(g.key), `${ty.key}: ${g.label}`);
    }
    assert.equal(t.includes("a FIX password"), ty.field_groups.includes("fix"), ty.key);
    assert.equal(t.includes("fake bank details"), ty.field_groups.some((g) => g.startsWith("bank_")), ty.key);
  }
});

test("wizard fails closed: no create control without can_write exactly true", () => {
  for (const env of [{ permissions: VIEW, vocabularies: VOCAB }, { vocabularies: VOCAB }, { permissions: { can_write: "true" } }, null]) {
    assert.equal(render(h(C.CreateWizard, { envelope: env })), "");
  }
});

test("wizard result: the key shown ONCE with the warning, and a test message ready", () => {
  const res = { id: "c1", slug: "harbor", issued: { api_key: "cpsim_live_" + "a".repeat(40) } };
  const t = text(render(h(C.CreateWizard, { envelope: { permissions: SU, vocabularies: VOCAB }, initialResult: res })));
  assert.ok(t.includes("Shown once") && t.includes("cpsim_live_aaaa") && t.includes("Send test message"));
});

test("detail: tabs follow the type; Import / Export is disabled with its cpsim02 note", () => {
  const want = { altruist: ["connection", "behaviour", "clock", "activity"], schwab: ["connection", "fix", "behaviour", "clock", "activity"],
    domestic_bank: ["connection", "bank", "behaviour", "clock", "activity"], international_bank: ["connection", "bank", "behaviour", "clock", "activity"] };
  for (const ty of TYPES) {
    const tabs = L.tabsFor(detail(ty.key), ty.key);
    assert.deepEqual(tabs.filter((t) => t.enabled).map((t) => t.key), want[ty.key], ty.key);
    assert.equal(tabs.find((t) => t.key === "import_export").enabled, false);
    const html = render(h(C.CounterpartyDetail, { envelope: detail(ty.key), requests: { rows: [] } }));
    assert.ok(/<button[^>]*disabled=""[^>]*data-tab="import_export"/.test(html), `${ty.key}: import/export disabled`);
  }
  const ie = text(render(h(C.CounterpartyDetail, { envelope: detail("altruist"), requests: { rows: [] }, initialTab: "import_export" })));
  assert.ok(ie.includes(L.IMPORT_EXPORT_NOTE));
});

test("detail tabs render their content: connection, FIX, bank, behaviour, clock, activity", () => {
  const conn = text(render(h(C.CounterpartyDetail, { envelope: detail("altruist"), requests: { rows: [] } })));
  for (const w of ["https://api.example.test/cpsim/c/harbor", "Copy", "cpsim_live_…9f3a", "Rotate key", "Revoke key", "Activate", "Send test message"]) {
    assert.ok(conn.includes(w), w);
  }
  const fix = text(render(h(C.CounterpartyDetail, { envelope: detail("schwab"), requests: { rows: [] }, initialTab: "fix" })));
  for (const w of ["FIX.4.4", "HOLLISWORKS", "9878", "Rotate FIX password", "no FIX engine"]) assert.ok(fix.includes(w), w);
  const bank = text(render(h(C.CounterpartyDetail, { envelope: detail("international_bank"), requests: { rows: [] }, initialTab: "bank" })));
  for (const w of ["CPSMXXA0XXX", "GB00CPSM", "Regenerate bank details", "Fake by construction"]) assert.ok(bank.includes(w), w);
  const beh = text(render(h(C.CounterpartyDetail, { envelope: detail("altruist"), requests: { rows: [] }, initialTab: "behaviour" })));
  assert.ok(beh.includes("Accept") && beh.includes("Webhooks"));
  const clk = text(render(h(C.CounterpartyDetail, { envelope: detail("altruist"), requests: { rows: [] }, initialTab: "clock" })));
  assert.ok(clk.includes("2026-11-11") && clk.includes("Advance one business day"));
  const act = text(render(h(C.CounterpartyDetail, { envelope: detail("altruist"), initialTab: "activity",
    requests: { rows: [{ id: "l1", at: "2026-11-10T15:00:00Z", method: "POST", path: "/cpsim/c/harbor/mirror", status: 200,
      auth_outcome: "ok", shape_valid: true, message_type: "test.ping" }] } })));
  assert.ok(act.includes("/cpsim/c/harbor/mirror") && act.includes("valid") && act.includes("test.ping"));
});

test("detail fails closed: a view-only or lost envelope shows the data but no action and no test message", () => {
  for (const perms of [VIEW, null, { can_write: 1 }]) {
    const env = detail("domestic_bank", perms, VIEW_VOCAB);
    for (const tab of ["connection", "bank", "behaviour", "clock"]) {
      const t = text(render(h(C.CounterpartyDetail, { envelope: env, requests: { rows: [] }, initialTab: tab })));
      for (const w of ["Rotate key", "Revoke key", "Activate", "Regenerate", "Advance one business day", "Send test message"]) {
        assert.ok(!t.includes(w), `${tab}: ${w}`);
      }
    }
  }
  assert.equal(render(h(C.CounterpartyDetail, { envelope: null })), "");
});

test("send test message: the reply is shown with its status and the SIMULATED header", () => {
  const reply = { status: 200, simulated_header: "true", reply: { message_id: "test-1", mirrored: true, received_at: "2026-11-11T15:00:00Z" } };
  const t = text(render(h(C.SendTestMessage, { counterpartyId: "c1", envelope: { permissions: SU }, initialReply: reply })));
  assert.ok(t.includes("HTTP 200") && t.includes("SIMULATED") && t.includes("mirrored") && t.includes("test-1"));
  assert.equal(render(h(C.SendTestMessage, { counterpartyId: "c1", envelope: { permissions: VIEW } })), "");
});

test("org access: toggles only with can_write; default shown off", () => {
  const env = { rows: [{ id: "o1", name: "Test Org", is_test: true, allowed: false }], permissions: SU, vocabularies: VOCAB };
  const html = render(h(C.OrgAccessPanel, { envelope: env }));
  assert.ok(text(html).includes("Test Org") && html.includes(">No<") && html.includes(">Allow</button>"));
  const v = render(h(C.OrgAccessPanel, { envelope: { ...env, permissions: VIEW } }));
  assert.ok(!v.includes("<button"), "no toggle without can_write");
});
