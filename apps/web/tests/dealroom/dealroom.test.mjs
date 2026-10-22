// DEALROOM1 — the REAL deal-room components rendered with react-dom/server from
// API-shaped envelopes (the shapes routers/deal_room.py returns). Write
// controls must come only from the server's envelope; a missing envelope or a
// missing `can_write` renders none (fail closed).
import assert from "node:assert/strict";
import { register } from "node:module";
import test from "node:test";

register("../market/jsxLoader.mjs", import.meta.url);

const { createElement: h } = await import("react");
const { renderToStaticMarkup } = await import("react-dom/server");
const dr = await import("../../lib/dealRoom.mjs");
const { default: DataRoom, InvestorPanel } = await import("../../components/dealroom/DataRoom.jsx");
const { default: AccessGrid, gridColumnDefs } = await import("../../components/dealroom/AccessGrid.jsx");
const { default: MyDealAccess } = await import("../../components/dealroom/MyDealAccess.jsx");

const render = (el) => renderToStaticMarkup(el);
const text = (html) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&");

const VOCAB = {
  levels: [
    { key: "public", label: "Public" }, { key: "after_agreements", label: "After agreements are signed" },
    { key: "after_approval", label: "After approval" }, { key: "named_only", label: "Named investors only" },
  ],
  statuses: [{ key: "requested", label: "Requested" }, { key: "granted", label: "Granted" }],
  eligibility_rules: [], approval_modes: [], sponsor_display: [], editable: [],
};
const folder = (id, label, level, open, extra = {}) => ({
  id, key: id, label, sort_order: Number(id.slice(1)), access_level: level, effective_level: level,
  access_level_label: VOCAB.levels.find((l) => l.key === level).label, parent_id: null, is_standard: true,
  open, lock_reason: open ? null : "Opens once you have signed the deal's agreements.",
  ...(open ? { documents: [{ id: "d1", file_name: "teaser.pdf", file_type: "application/pdf",
    file_size_bytes: 10, status: "approved", created_at: "2026-10-22T00:00:00Z" }] } : {}),
  ...extra,
});
const AGREEMENTS = [
  { id: "a1", title: "Non-Disclosure and Non-Circumvention Agreement", sort_order: 1, signed_at: null, signature_id: null },
  { id: "a2", title: "Joinder to Confidentiality Agreement", sort_order: 2, signed_at: null, signature_id: null },
];
const investorEnv = (access, agreements = AGREEMENTS) => ({
  deal: { id: "deal1", name: "Project X" },
  rights: { role: "none", can_manage: false },
  permissions: { can_read: true, can_write: false, is_super_admin: false },
  folders: [folder("f1", "01 Overview", "public", true), folder("f2", "02 Offering Memorandum", "after_agreements", false)],
  my_access: access, agreements, vocabularies: VOCAB,
  investing_entities: [{ id: "e1", name: "Jane Doe" }, { id: "e2", name: "Doe Family LLC" }],
});

test("investor flow: request → not eligible → sign in order → awaiting / granted", () => {
  assert.equal(dr.investorStep(investorEnv(null)).step, "request");
  const ne = dr.investorStep(investorEnv({ status: "not_eligible", missing: ["Accreditation is not verified."] }));
  assert.deepEqual(ne, { step: "not_eligible", missing: ["Accreditation is not verified."] });
  const s1 = dr.investorStep(investorEnv({ status: "requested" }));
  assert.equal(s1.step, "sign");
  assert.equal(s1.next.id, "a1", "the first agreement in sort order comes first");
  assert.equal(s1.remaining, 2);
  const s2 = dr.investorStep(investorEnv({ status: "requested" },
    [{ ...AGREEMENTS[0], signed_at: "2026-10-22T10:00:00Z", signature_id: "s1" }, AGREEMENTS[1]]));
  assert.equal(s2.next.id, "a2");
  assert.equal(dr.investorStep(investorEnv({ status: "signed_awaiting" })).step, "awaiting");
  assert.equal(dr.investorStep(investorEnv({ status: "granted" })).step, "granted");
  assert.equal(dr.investorStep(null).step, "unavailable");
});

test("investor panel renders each step from the envelope", () => {
  const req = text(render(h(InvestorPanel, { envelope: investorEnv(null) })));
  assert.match(req, /Request access/);
  assert.match(req, /Doe Family LLC/, "the investing-entity choice lists the server's entities");
  const ne = text(render(h(InvestorPanel, { envelope: investorEnv({ status: "not_eligible", status_label: "Not eligible",
    missing: ["Accreditation is not verified (currently not_verified)."] }) })));
  assert.match(ne, /Accreditation is not verified/);
  const sign = render(h(InvestorPanel, { envelope: investorEnv({ status: "requested", status_label: "Requested" }) }));
  assert.match(sign, /data-step="sign"/);
  assert.match(text(sign), /Next:\s+Non-Disclosure/);
});

test("locked folders show the reason and NO file names; open ones list documents", () => {
  const html = render(h(DataRoom, { initialEnvelope: investorEnv({ status: "requested" }) }));
  const t = text(html);
  assert.match(t, /Opens once you have signed the deal's agreements\./);
  assert.match(t, /teaser\.pdf/, "the public folder's document is listed");
  const rows = dr.folderRows(investorEnv({ status: "requested" }));
  const locked = rows.find((r) => r.label === "02 Offering Memorandum");
  assert.equal(locked.open, false);
  assert.equal(locked.documents, null, "a closed folder carries no document list at all");
  assert.ok(!/Upload|Add subfolder|Room settings|manager-panel/.test(html), "no write control for an investor");
});

test("investor view never renders other investors: the envelope has no such field and nothing is shown", () => {
  const env = investorEnv({ status: "granted" });
  const html = render(h(DataRoom, { initialEnvelope: env }));
  assert.ok(!/interested|investors? (have|has)|members interested/i.test(text(html)));
});

test("publisher controls render only when can_write === true (no truthy fallback)", () => {
  const manage = {
    ...investorEnv(null), rights: { role: "publisher", can_manage: true },
    permissions: { can_read: true, can_write: true }, settings: { eligibility_rule: "accredited",
      approval_mode: "automatic", sponsor_display: "name" }, delegates: [],
    folders: [folder("f1", "01 Overview", "public", true, { named: [] })],
  };
  const html = render(h(DataRoom, { initialEnvelope: manage, templates: [] }));
  assert.match(html, /Upload/);
  assert.match(html, /data-testid="manager-panel"/);
  for (const perms of [undefined, null, {}, { can_write: "true" }, { can_write: 1 }]) {
    const env = { ...manage, permissions: perms };
    assert.equal(dr.canWrite(env), false, `can_write fails closed for ${JSON.stringify(perms)}`);
    assert.ok(!/manager-panel|Upload/.test(render(h(DataRoom, { initialEnvelope: env, templates: [] }))));
  }
});

const GRID = {
  rows: [{
    id: "acc1", deal_id: "deal1", deal_name: "Project X", publisher_name: "Acme Advisors", firm_name: "2nd Act",
    investor_user_id: "u1", investor_name: "Jane Doe", entity_name: "Doe Family LLC",
    eligibility: { accreditation_status: "third_party_verified", verified_date: "2026-01-02" },
    agreements: { platform_nda: "2026-10-22T10:00:00+00:00", platform_joinder: null },
    level_label: null, status: "signed_awaiting", status_label: "Signed, awaiting approval",
    decided_by: null, decided_at: null, group: "Project X",
  }],
  total: 1, page: 0, page_size: 50, group_by: "deal",
  agreement_columns: [{ key: "platform_nda", title: "NDA" }, { key: "platform_joinder", title: "Joinder" }],
  permissions: { can_read: true, can_write: true },
  vocabularies: { ...VOCAB, actions: ["approve", "decline", "revoke"], group_by: ["deal", "publisher", "firm", "all"],
    grant_levels: VOCAB.levels.slice(1, 3) },
};

test("grid: columns per required agreement; cells show signed date or —", () => {
  const cols = dr.gridColumns(GRID);
  assert.deepEqual(cols.filter((c) => c.agreementKey).map((c) => c.headerName), ["NDA", "Joinder"]);
  const nda = cols.find((c) => c.agreementKey === "platform_nda");
  const joinder = cols.find((c) => c.agreementKey === "platform_joinder");
  assert.equal(dr.gridCell(GRID.rows[0], nda), "2026-10-22");
  assert.equal(dr.gridCell(GRID.rows[0], joinder), "—");
  assert.equal(dr.gridCell(GRID.rows[0], cols.find((c) => c.field === "eligibility")), "third_party_verified · 2026-01-02");
});

test("grid: approve / decline / revoke render from the envelope only", () => {
  const html = render(h(AccessGrid, { initialEnvelope: GRID }));
  assert.match(html, /data-testid="grid-actions"/);
  for (const label of ["Approve", "Decline", "Revoke"]) assert.match(html, new RegExp(`>${label}<`));
  assert.match(text(html), /Jane Doe/);
  const noWrite = { ...GRID, permissions: { can_read: true } };
  assert.deepEqual(dr.gridActions(noWrite), []);
  const html2 = render(h(AccessGrid, { initialEnvelope: noWrite }));
  assert.ok(!/grid-actions|>Approve</.test(html2), "no action control without can_write");
  assert.ok(!gridColumnDefs(noWrite, { selected: [], toggle() {} }).some((c) => c.field === "select"),
    "no selection column without can_write");
  assert.ok(gridColumnDefs(GRID, { selected: [], toggle() {} }).some((c) => c.field === "select"));
});

test("my deal access lists the investor's own deals, signatures and signed copies", () => {
  const html = render(h(MyDealAccess, { rows: [{
    deal_id: "deal1", deal_name: "Project X", status_label: "Granted", level_label: "After agreements are signed",
    entity_name: "Jane Doe", signatures: [{ signature_id: "s1", title: "NDA", version: 1,
      signed_at: "2026-10-22T10:00:00+00:00", copy_href: "/api/deal-room/signatures/s1/copy" }],
  }] }));
  assert.match(html, /\/api\/deal-room\/signatures\/s1\/copy/);
  assert.match(text(html), /signed 2026-10-22/);
  assert.match(text(render(h(MyDealAccess, { rows: [] }))), /not requested access/);
});
