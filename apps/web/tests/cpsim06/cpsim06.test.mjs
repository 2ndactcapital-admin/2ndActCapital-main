// CPSIM06 — "Open account at custodian" (SIMULATED custodian) and Admin → Counterparties → Realtime, rendered with
// react-dom/server from envelopes shaped exactly like the API returns them (GET /admin/households/{id},
// GET /admin/households/{id}/custodian-account, Check / Open results, GET /admin/counterparties/{id}/realtime).
// Every control renders only on the server's exact true (fails closed); a tax id appears as its last four only; a date
// of birth never appears; problems render next to their field.
import assert from "node:assert/strict";
import { register } from "node:module";
import test from "node:test";

register("../market/jsxLoader.mjs", import.meta.url);

const { createElement: h } = await import("react");
const { renderToStaticMarkup } = await import("react-dom/server");
const O = await import("../../components/households/OpenCustodianAccount.jsx");
const C = await import("../../components/counterparties/Counterparties.jsx");
const L = await import("../../lib/custodianAccountOpening.mjs");

const render = (el) => renderToStaticMarkup(el);
const text = (html) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&")
  .replace(/&quot;/g, '"').replace(/\s+/g, " ");
const count = (html, rx) => (html.match(rx) || []).length;

const SUPPORTED = [
  { key: "individual", label: "Individual", shape: "single", roles: ["owner"], registration_label: "Individual", tax_status_label: "Taxable" },
  { key: "jtwros", label: "Joint Tenants With Right of Survivorship", shape: "joint", roles: ["owner", "joint_owner"], registration_label: "Joint", tax_status_label: "Taxable" },
  { key: "trust", label: "Trust (revocable or irrevocable)", shape: "trust", roles: ["trust", "trustee"], registration_label: "Trust", tax_status_label: "Taxable" },
  { key: "roth_ira", label: "Roth IRA", shape: "single", roles: ["owner"], registration_label: "Roth IRA", tax_status_label: "Tax-exempt" },
  { key: "utma", label: "UTMA", shape: "custodial", roles: ["minor", "custodian"], registration_label: "Other", tax_status_label: "Taxable" },
];
const REASON = "not supported by the custodian's API for account opening — open it through the custodian's own process";
const UNSUPPORTED = [
  { key: "sep_ira", label: "SEP IRA", reason: REASON }, { key: "partnership", label: "Partnership", reason: REASON },
  { key: "donor_advised_fund", label: "Donor-Advised Fund", reason: REASON },
];
const MEMBERS = [
  { entity_id: "p1", display_name: "Fixture Adult", entity_type: "individual", kind: "person", legal_name: "Fixture Adult",
    email: "adult@example.test", phone: "512-555-0100",
    residential_address: { street1: "1 Main St", city: "Austin", state: "TX", postal_code: "78701", country: "US" },
    mailing_address: null, citizenship: "us_citizen", employment: { status: "employed", employer: "Fixture Co" },
    date_of_birth_on_record: true, is_minor: false, deceased: false,
    tax_id: { on_record: "hash_only", last4: "4321", kind: "itin" } },
  { entity_id: "p2", display_name: "Fixture Minor", entity_type: "individual", kind: "person", legal_name: "Fixture Minor",
    email: null, phone: null, residential_address: null, mailing_address: null, citizenship: null, employment: null,
    date_of_birth_on_record: false, is_minor: null, deceased: false, tax_id: { on_record: "none", last4: null, kind: null } },
  { entity_id: "t1", display_name: "Fixture Trust", entity_type: "trust", kind: "entity", legal_name: "Fixture Trust",
    email: null, phone: null, residential_address: null, mailing_address: null, citizenship: null, employment: null,
    date_of_birth_on_record: false, is_minor: null, deceased: false, tax_id: { on_record: "full", last4: "7788", kind: "ein" } },
];
const formEnv = (extra = {}) => ({
  household: { id: "h1", name: "Fixture Household" }, label: "SIMULATED",
  availability: { available: true, reason: null, environment: "simulator", counterparty: "fixture" },
  members: MEMBERS, suggestions: { trustees: { t1: ["p1"] }, parents: {}, beneficiaries: {} },
  permissions: { can_read: true, can_write: true, can_open: true, read_permission: "manage_members", write_permission: "manage_members" },
  vocabularies: {
    account_types: { supported: SUPPORTED, unsupported: UNSUPPORTED },
    roles: [{ key: "owner", label: "Owner" }, { key: "joint_owner", label: "Joint owner" }, { key: "trust", label: "Trust" },
      { key: "trustee", label: "Trustee" }, { key: "minor", label: "Minor" }, { key: "custodian", label: "Custodian (adult)" }],
    citizenship: [{ key: "us_citizen", label: "US citizen" }, { key: "visa", label: "Visa holder" }],
    employment_statuses: [{ key: "employed", label: "Employed" }], visa_types: ["H1B"],
    tiers: [{ key: "primary", label: "Primary" }, { key: "contingent", label: "Contingent" }],
    editable: ["owners", "beneficiaries", "trusted_contact", "account_type"], inline_editable: [],
  },
  ...extra,
});

test("household action: present only on can_open === true AND can_write === true; otherwise the reason or nothing", () => {
  const yes = { id: "h1", permissions: { can_write: true }, custodian_account_opening: { available: true, can_open: true } };
  assert.deepEqual(L.actionState(yes), { show: true, reason: null });
  const html = render(h(O.default, { household: yes }));
  assert.match(text(html), /Open account at custodian/);
  assert.equal(count(html, /<button/g), 1);
  const reason = "account opening needs Altruist Realtime API access (not granted)";
  const no = { id: "h1", permissions: { can_write: true }, custodian_account_opening: { available: false, can_open: false, reason } };
  const nhtml = render(h(O.default, { household: no }));
  assert.equal(count(nhtml, /<button/g), 0, "no button when unavailable");
  assert.match(text(nhtml), /account opening needs Altruist Realtime API access \(not granted\)/);
  // can_open "true" (a string), a lost flag, or a reader: nothing at all (fails closed)
  for (const d of [{ ...yes, custodian_account_opening: { can_open: "true" } }, { id: "h1", permissions: { can_write: true } },
    { ...yes, permissions: { can_write: false } }, null]) {
    assert.equal(render(h(O.default, { household: d })), "", "renders nothing");
  }
  assert.equal(L.actionState({ ...no, permissions: { can_write: false } }).reason, null, "a reader is not told the reason");
});

test("form: YES types enabled, every NO type shown DISABLED with the custodian's reason; SIMULATED banner; Check and Open", () => {
  const html = render(h(O.OpenCustodianAccountForm, { envelope: formEnv() }));
  const t = text(html);
  assert.match(html, /data-testid="simulated-banner"/);
  assert.match(t, /SIMULATED/);
  assert.equal(count(html, /data-supported="yes"/g), SUPPORTED.length, "every supported type offered");
  assert.equal(count(html, /data-supported="no"/g), UNSUPPORTED.length, "every unsupported type listed");
  assert.equal(count(html, /<option[^>]*disabled=""[^>]*data-supported="no"/g), UNSUPPORTED.length, "each NO type disabled");
  for (const u of UNSUPPORTED) assert.ok(t.includes(`${u.label} — ${REASON}`), `${u.label} shows the reason`);
  assert.match(t, /Roth IRA \(recorded as Roth IRA\)/);
  assert.match(t, /Check/);
  assert.match(t, /Open/);
});

test("form: unavailable or can_open not exactly true → the reason, no control", () => {
  const reason = "this household is not a demo household — a simulated custodian opens accounts only for a test organisation or for a demo household";
  for (const env of [formEnv({ availability: { available: false, reason } }),
    formEnv({ permissions: { can_read: true, can_write: true, can_open: false } }),
    formEnv({ permissions: undefined })]) {
    const html = render(h(O.OpenCustodianAccountForm, { envelope: env }));
    assert.equal(count(html, /<button/g), 0);
    assert.equal(count(html, /<select/g), 0);
    assert.match(html, /data-testid="custodian-open-unavailable"/);
  }
  assert.match(text(render(h(O.OpenCustodianAccountForm, { envelope: formEnv({ availability: { available: false, reason } }) }))),
    /not a demo household/);
});

test("pre-fill: a tax id is shown as its LAST FOUR only; a date of birth never appears; typed inputs only where needed", () => {
  const env = formEnv();
  const st = L.initialState(env, "trust");
  assert.equal(st.owners[0].entity_id, "t1", "the only trust is pre-picked");
  assert.equal(st.owners[1].entity_id, "p1", "its trustee from the CRM relationship");
  const html = render(h(O.OpenCustodianAccountForm, { envelope: env, initialState: st }));
  const t = text(html);
  assert.match(t, /Tax ID: •••• 7788 on record — read at send time/);
  assert.match(t, /Tax ID: •••• 4321 on record — type the full number for this request/);
  assert.equal(count(html, /[0-9]{9}/g), 0, "no 9-digit run anywhere");
  assert.equal(count(html, /\b(19|20)[0-9]{2}-[0-9]{2}-[0-9]{2}\b/g), 0, "no date of birth rendered");
  assert.match(t, /Date of birth: on record/);
  // the trustee's tax id is hash-only → a password-type input; the trust's is on record → no input for it
  assert.equal(count(html, /type="password"/g), 1);
});

test("problems render next to their field — owners, beneficiaries, the type, the trusted contact — every one", () => {
  const env = formEnv();
  const st = { ...L.initialState(env, "trust"), beneficiaries: [{ tier: "primary", name: "A", kind: "person", percentage: "50" },
    { tier: "primary", name: "B", kind: "person", percentage: "49.99" }] };
  const problems = [
    { field: "owners[1].residential_address.street1", code: "po_box", message: "a residential address cannot be a PO Box", source: "custodian" },
    { field: "owners[1].tax_id", code: "crm_mismatch", message: "does not match the tax ID on the CRM record (•••• 4321)", source: "hollisworks" },
    { field: "owners[0].tax_id.value", code: "ein_prefix", message: "not a valid EIN", source: "custodian" },
    { field: "beneficiaries.primary", code: "percent_total", message: "primary beneficiaries must total exactly 100% (they total 99.99%)" },
    { field: "beneficiaries[1].kind", code: "invalid", message: "kind is person or entity" },
    { field: "trusted_contact", code: "is_owner", message: "the trusted contact cannot be an owner of the account" },
    { field: "account_type", code: "unsupported_type", message: "SEP IRA: " + REASON },
  ];
  const html = render(h(O.OpenCustodianAccountForm, { envelope: env, initialState: st, initialResult: { ok: false, problems } }));
  assert.equal(count(html, /data-problem-field=/g), problems.length, "every problem listed");
  const owner1 = html.split('data-testid="owner-problems-1"')[1].split("</ul>")[0];
  assert.match(owner1, /residential address · street1/);
  assert.match(owner1, /does not match the tax ID on the CRM record/);
  assert.match(html.split('data-testid="owner-problems-0"')[1].split("</ul>")[0], /tax id · value/);
  assert.match(html.split('data-testid="beneficiaries-problems"')[1].split("</ul>")[0], /99\.99%/);
  assert.match(html.split('data-testid="beneficiary-problems-1"')[1].split("</ul>")[0], /kind is person or entity/);
  assert.match(html.split('data-testid="trusted-problems"')[1].split("</ul>")[0], /cannot be an owner/);
  assert.match(html.split('data-testid="type-problems"')[1].split("</ul>")[0], /SEP IRA/);
  const g = L.groupProblems(problems);
  assert.deepEqual(Object.keys(g).sort(), ["account_type", "beneficiaries", "beneficiaries.1", "owners.0", "owners.1", "trusted_contact"]);
});

test("opened: the outcome line carries the masked number, type, ACTIVE + date_open and the sync — never a full number", () => {
  const result = { ok: true, opened: true, repeat: false, account: { id: "a1", account_number_masked: "**********6789" },
    counterparty: { account_id: "rta-x", account_type: "ROTH IRA", status: "ACTIVE", date_open: "2026/11/10", account_number_masked: "•••• 6789" },
    sync: { synced: true }, problems: [] };
  const html = render(h(O.OpenCustodianAccountForm, { envelope: formEnv(), initialResult: result }));
  assert.match(text(html), /Opened at the simulated custodian: \*+6789 · ROTH IRA · ACTIVE since 2026\/11\/10 · synced/);
  assert.equal(count(html, /CPSIM[0-9]/g), 0, "the counterparty number never appears in full");
});

test("buildBody: no org_id; typed values only when typed; percentages stay decimal text", () => {
  const b = L.buildBody({ account_type: "roth_ira", owners: [{ role: "owner", entity_id: "p1", overrides: { tax_id_value: "x", tax_id_kind: "itin", employment_status: "retired" } },
    { role: "owner", entity_id: "", overrides: {} }],
  beneficiaries: [{ tier: "primary", name: "A", kind: "person", percentage: "100.00" }, { tier: "primary", name: "", percentage: "" }],
  trusted_contact: { name: "", email: "", phone: "" } });
  assert.equal("org_id" in b, false);
  assert.equal(b.owners.length, 1);
  assert.deepEqual(b.owners[0].overrides, { employment: { status: "retired", employer: null, occupation: null }, tax_id: { kind: "itin", value: "x" } });
  assert.equal(b.beneficiaries.length, 1);
  assert.equal(b.beneficiaries[0].percentage, "100.00");
  assert.equal(b.trusted_contact, null);
});

// ── Admin → Counterparties → Realtime ──────────────────────────────────────────────────────────────────────────────
const realtimeEnv = (canW = true) => ({
  simulated: true,
  permissions: { can_read: canW, can_write: canW, is_super_admin: canW, read_permission: "super_admin", write_permission: "super_admin" },
  vocabularies: { editable: canW ? ["book"] : [], inline_editable: [] },
  users: [{ id: "u1", username: "fixture-user", created_at: "2026-11-12T10:00:00Z", revoked_at: null },
    { id: "u2", username: "old-user", created_at: "2026-11-11T10:00:00Z", revoked_at: "2026-11-12T09:00:00Z" }],
  contacts: [{ id: "rtc-a", legal_name: "Fixture Adult", email: "adult@example.test", tax_id_last4: "4321", age_years: 40, problems: [] },
    { id: "rtc-b", legal_name: "Fixture Bad", email: "bad@example.test", tax_id_last4: "0000", age_years: 30,
      problems: [{ field: "tax_id.value", code: "ssn_serial", message: "not a valid SSN" }] }],
  drafts: [{ id: "rtd-1", account_type: "roth_ira", owners: [{ contact_id: "rtc-a", role: "owner" }], status: "opened",
    account_id: "rta-1", account_number: "CPSIM123456789", date_open: "2026-11-10" }],
  validation_log: [{ id: "l1", draft_id: "rtd-2", action: "submit", outcome: "refused", problem_count: 2, created_at: "2026-11-12T10:00:00Z",
    problems: [{ field: "owners[0].residential_address.street1", message: "a residential address cannot be a PO Box" },
      { field: "account_type", message: "SEP IRA: " + REASON }] }],
  realtime: { account_types: [{ key: "roth_ira", label: "Roth IRA" }] },
  notes: ["SIMULATED — Realtime-API-shaped account opening. Every path and field is ASSUMED."],
});

test("Realtime tab: users, drafts + opened accounts, contacts (last four only), the validation log with every problem", () => {
  const html = render(h(C.RealtimeTab, { id: "c1", initialRealtime: realtimeEnv(true) }));
  const t = text(html);
  assert.equal(count(html, /data-user=/g), 2);
  assert.equal(count(html, /data-draft=/g), 1);
  assert.match(t, /Roth IRA/);
  assert.match(t, /rta-1 · \*\*\*\*6789/);
  assert.equal(count(html, /CPSIM[0-9]/g), 0, "the opened account number shows as its last four");
  assert.equal(count(html, /data-testid="contact-taxid"/g), 2);
  assert.match(t, /•••• 4321/);
  // the rendered TEXT (the markup's colour classes, e.g. #64748B, carry digit runs of their own)
  assert.equal(count(t, /[0-9]{5}/g), 0, "no tax id longer than four digits anywhere");
  assert.equal(count(html, /data-log-problem=/g), 2, "every logged problem");
  assert.match(t, /ASSUMED/);
  assert.equal(count(html, />Revoke</g), 1, "revoke only for the live user");
  assert.match(html, /data-testid="realtime-user-create"/);
});

test("Realtime tab: a caller without write sees no control (fails closed)", () => {
  const html = render(h(C.RealtimeTab, { id: "c1", initialRealtime: realtimeEnv(false) }));
  assert.equal(count(html, /<button/g), 0);
  assert.equal(count(html, /<form/g), 0);
  const lost = realtimeEnv(true);
  delete lost.permissions;
  assert.equal(count(render(h(C.RealtimeTab, { id: "c1", initialRealtime: lost })), /<button/g), 0, "lost envelope → no control");
});

test("Issued once: the new user's password is labelled and shown only in the shown-once panel", () => {
  const html = render(h(C.IssuedSecrets, { issued: { realtime_password: "cpsim_rtpw_FIXTUREONLY" } }));
  assert.match(text(html), /Shown once/);
  assert.match(text(html), /Realtime user password/);
});
