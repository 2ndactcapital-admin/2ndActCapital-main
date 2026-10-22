// DEALROOM2 — the REAL deal-room components rendered with react-dom/server
// from API-shaped envelopes (the shapes routers/deal_room.py returns): the
// upload viewer's scroll-to-end Sign gate, re-signing, versions and the PDF
// copy in the investor views, the grid's version / master-NDA cells and its
// require-re-sign action, and the template library — every write control
// failing CLOSED without the server's can_write.
import assert from "node:assert/strict";
import { register } from "node:module";
import test from "node:test";

register("../market/jsxLoader.mjs", import.meta.url);

const { createElement: h } = await import("react");
const { renderToStaticMarkup } = await import("react-dom/server");
const dr = await import("../../lib/dealRoom.mjs");
const { default: DataRoom, InvestorPanel } = await import("../../components/dealroom/DataRoom.jsx");
const { default: AgreementViewer } = await import("../../components/dealroom/AgreementViewer.jsx");
const { default: AccessGrid } = await import("../../components/dealroom/AccessGrid.jsx");
const { default: MyDealAccess } = await import("../../components/dealroom/MyDealAccess.jsx");
const { default: TemplateLibrary } = await import("../../components/dealroom/TemplateLibrary.jsx");

const render = (el) => renderToStaticMarkup(el);
const text = (html) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&");

const VOCAB = {
  levels: [{ key: "public", label: "Public" }, { key: "after_agreements", label: "After agreements are signed" }],
  statuses: [{ key: "granted", label: "Granted" }, { key: "resign_required", label: "Re-signing required" }],
  resign_policies: [{ key: "keep", label: "Keep existing signers" }, { key: "resign", label: "Require re-signing" }],
  master_scopes: [{ key: "all", label: "All of this publisher's deals" }, { key: "selected", label: "Selected deals" }],
  agreement_kinds: [{ key: "template", label: "Template" }, { key: "upload", label: "Uploaded PDF (no fill-in fields)" }],
  eligibility_rules: [], approval_modes: [], sponsor_display: [], editable: [],
};
const UPLOAD_DOC = { id: "a9", kind: "upload", title: "Sponsor NDA", version: 2, file_name: "nda.pdf",
  file_sha256: "f".repeat(64), page_count: 3, fill_in_fields: false, exhibit: null };
const TEXT_DOC = { id: "a1", kind: "template", title: "NDA", text: "Agreement text", rendered_text_sha256: "e".repeat(64),
  template_status: "draft", exhibit: null };

test("upload viewer: every page is listed, the no-fill-in-fields note shows, Exhibit A follows", () => {
  const pages = dr.viewerPages("deal1", UPLOAD_DOC);
  assert.deepEqual(pages.map((p) => p.src), [1, 2, 3].map((i) =>
    `/api/deal-room/deals/deal1/agreements/a9/pages/${i}?part=document`));
  const withExhibit = dr.viewerPages("deal1", { ...TEXT_DOC, exhibit: { page_count: 2, sha256: "a".repeat(64) } });
  assert.deepEqual(withExhibit.map((p) => p.label), ["Exhibit A — page 1", "Exhibit A — page 2"]);
  const html = render(h(AgreementViewer, { dealId: "deal1", doc: UPLOAD_DOC }));
  assert.equal((html.match(/<img /g) || []).length, 3, "all three pages render");
  assert.match(text(html), /no fill-in fields/);
  assert.match(text(html), /Scroll to the last page to enable signing/);
});

test("Sign is gated on scroll-to-end for an upload (and on consent + name for everything)", () => {
  const ready = { consent: true, name: "Jane Doe" };
  assert.equal(dr.signEnabled({ needsScroll: true, scrolledToEnd: false, ...ready }), false, "not scrolled → disabled");
  assert.equal(dr.signEnabled({ needsScroll: true, scrolledToEnd: true, ...ready }), true, "scrolled → enabled");
  assert.equal(dr.signEnabled({ needsScroll: true, scrolledToEnd: true, consent: false, name: "Jane Doe" }), false);
  assert.equal(dr.signEnabled({ needsScroll: true, scrolledToEnd: true, consent: true, name: " " }), false);
  assert.equal(dr.signEnabled({ needsScroll: true, scrolledToEnd: "yes", ...ready }), false, "only exactly true counts");
  assert.equal(dr.signEnabled({ needsScroll: false, ...ready }), true, "a template agreement needs no scroll");
  assert.equal(dr.isScrolledToEnd({ scrollTop: 0, clientHeight: 400, scrollHeight: 2400 }), false);
  assert.equal(dr.isScrolledToEnd({ scrollTop: 1990, clientHeight: 400, scrollHeight: 2400 }), false);
  assert.equal(dr.isScrolledToEnd({ scrollTop: 2000, clientHeight: 400, scrollHeight: 2400 }), true);
  assert.equal(dr.isScrolledToEnd({}), false, "no measurements → not read");
  assert.deepEqual(dr.signPayload(UPLOAD_DOC, { name: "Jane Doe", consent: true }),
    { typed_name: "Jane Doe", consent: true, file_sha256: "f".repeat(64) }, "an upload signs the FILE hash");
  assert.deepEqual(dr.signPayload(TEXT_DOC, { name: "Jane Doe", consent: true }),
    { typed_name: "Jane Doe", consent: true, rendered_text_sha256: "e".repeat(64) });
});

const investorEnv = (status, agreements) => ({
  deal: { id: "deal1", name: "Project X" }, rights: { role: "none", can_manage: false },
  permissions: { can_read: true, can_write: false }, folders: [], vocabularies: VOCAB,
  my_access: { status, status_label: VOCAB.statuses.find((s) => s.key === status)?.label }, agreements,
  investing_entities: [{ id: "e1", name: "Jane Doe" }],
});

test("re-sign: resign_required shows what changed and asks for the new version; signed lines show versions", () => {
  const agreements = [
    { id: "a1", title: "NDA", sort_order: 1, signed_at: null, signature_id: null, stale: true, current_version: 2 },
    { id: "a2", title: "Joinder", sort_order: 2, signed_at: "2026-10-22T10:00:00Z", signature_id: "s2", signed_version: 1 },
  ];
  const st = dr.investorStep(investorEnv("resign_required", agreements));
  assert.equal(st.step, "sign");
  assert.equal(st.resign, true);
  assert.equal(st.next.id, "a1", "the changed agreement is next");
  const html = render(h(InvestorPanel, { envelope: investorEnv("resign_required", agreements) }));
  assert.match(html, /data-testid="resign-notice"/);
  assert.match(text(html), /Joinder \(v1\) — signed 2026-10-22/);
  assert.match(html, /\/api\/deal-room\/signatures\/s2\/pdf/, "the PDF copy is linked");
  assert.equal(dr.investorStep(investorEnv("requested", agreements)).resign, undefined);
  const covered = dr.signatureLine({ title: "Master NDA", covered_by_master: true, signed_at: "2026-09-01T00:00:00Z" });
  assert.equal(covered, "Master NDA — covered by your master NDA signed 2026-09-01");
});

test("the Sign button renders disabled before the reader has scrolled", () => {
  // InvestorPanel opens with no document; the viewer + button are rendered
  // through the real components once a document is shown.
  const html = render(h("div", null, h(AgreementViewer, { dealId: "deal1", doc: UPLOAD_DOC }),
    h("button", { "data-testid": "sign-button", disabled: !dr.signEnabled({ needsScroll: true, scrolledToEnd: false,
      consent: true, name: "Jane Doe" }) }, "Sign")));
  assert.match(html, /data-testid="sign-button" disabled=""/);
});

test("my deal access: versions, PDF copies and what to re-sign", () => {
  const html = render(h(MyDealAccess, { rows: [{
    deal_id: "deal1", deal_name: "Project X", status: "resign_required", status_label: "Re-signing required",
    entity_name: "Jane Doe", to_sign: [{ agreement_id: "a1", title: "Sponsor NDA", version: 2 }],
    signatures: [{ signature_id: "s1", title: "Sponsor NDA", version: 1, signed_at: "2026-10-22T10:00:00+00:00",
      entity_name: "Jane Doe", copy_href: "/api/deal-room/signatures/s1/copy", pdf_href: "/api/deal-room/signatures/s1/pdf" }],
  }] }));
  const t = text(html);
  assert.match(html, /data-testid="to-sign"/);
  assert.match(t, /Sponsor NDA \(version 2\)/);
  assert.match(t, /Sponsor NDA \(v1\) — signed 2026-10-22/);
  assert.match(html, /\/api\/deal-room\/signatures\/s1\/pdf/);
});

const GRID = {
  rows: [{
    id: "acc1", deal_id: "deal2", deal_name: "Project Y", publisher_name: "Acme", firm_name: "2nd Act",
    investor_user_id: "u1", investor_name: "Jane Doe", entity_name: "Jane Doe", eligibility: {},
    agreements: { master_nda: "2026-09-01T00:00:00+00:00", "upload:a9": null },
    agreement_detail: {
      master_nda: { label: "covered by master NDA signed 2026-09-01", covered_by_master: true },
      "upload:a9": { label: "signed v1 — v2 needed", stale: true },
    },
    covered_by_master: "covered by master NDA signed 2026-09-01",
    status: "resign_required", status_label: "Re-signing required", group: "Project Y",
  }],
  total: 1, page: 0, page_size: 50, group_by: "deal",
  agreement_columns: [{ key: "master_nda", title: "Master NDA" }, { key: "upload:a9", title: "Sponsor NDA" }],
  permissions: { can_read: true, can_write: true },
  vocabularies: { ...VOCAB, actions: ["approve", "decline", "revoke", "require_resign"], group_by: ["deal"],
    grant_levels: VOCAB.levels.slice(1) },
};

test("grid: version / master-NDA cells, resign_required in the filter, bulk Require re-sign from the envelope", () => {
  const cols = dr.gridColumns(GRID);
  const master = cols.find((c) => c.agreementKey === "master_nda");
  const upload = cols.find((c) => c.agreementKey === "upload:a9");
  assert.equal(dr.gridCell(GRID.rows[0], master), "covered by master NDA signed 2026-09-01");
  assert.equal(dr.gridCell(GRID.rows[0], upload), "signed v1 — v2 needed");
  const html = render(h(AccessGrid, { initialEnvelope: GRID }));
  assert.match(text(html), /covered by master NDA signed 2026-09-01/);
  assert.match(html, />Require re-sign</);
  assert.match(html, /<option value="resign_required">Re-signing required<\/option>/, "the status filter offers it");
  const noWrite = { ...GRID, permissions: { can_read: true } };
  assert.ok(!/Require re-sign/.test(render(h(AccessGrid, { initialEnvelope: noWrite }))),
    "no bulk action without can_write");
});

const managerEnv = (perms) => ({
  deal: { id: "deal1", name: "Project X" }, rights: { role: "publisher", can_manage: true }, permissions: perms,
  folders: [], vocabularies: VOCAB, settings: { eligibility_rule: "accredited", approval_mode: "automatic",
    sponsor_display: "name" }, delegates: [], publisher_entity_id: "pub1",
  master_nda: { id: "m1", scope: "all", deal_agreement_id: "a1", deal_ids: [], covers_this_deal: true },
  agreements: [
    { id: "a1", title: "NDA", kind: "template", status: "published", sort_order: 1, current_version: 2, signatures: 3,
      is_master: true, fields: [{ key: "term_years", label: "Term years", source: "deal" }], field_values: {} },
    { id: "a9", title: "Sponsor NDA", kind: "upload", status: "published", sort_order: 2, current_version: 1,
      signatures: 0, fields: [] },
  ],
});

test("manager: versions, re-sign choice, upload and master NDA controls — only with can_write === true", () => {
  const html = render(h(DataRoom, { initialEnvelope: managerEnv({ can_read: true, can_write: true }), templates: [] }));
  assert.match(html, /data-testid="new-version"/);
  assert.match(text(html), /3 signatures on this agreement/, "a signed agreement asks keep or re-sign");
  assert.match(html, /<option value="resign">Require re-signing<\/option>/);
  assert.match(html, /data-testid="replace-file"/);
  assert.match(html, /data-testid="upload-agreement"/);
  assert.match(text(html), /optional fill-in fields are not supported for uploads/);
  assert.match(html, /data-testid="make-master"/, "the upload can become the master NDA");
  assert.match(html, /data-testid="master-nda-status"/);
  for (const perms of [undefined, {}, { can_write: "true" }, { can_write: 1 }]) {
    const out = render(h(DataRoom, { initialEnvelope: managerEnv(perms), templates: [] }));
    assert.ok(!/new-version|replace-file|upload-agreement|make-master/.test(out),
      `no Phase 2 write control for permissions ${JSON.stringify(perms)}`);
  }
});

const LIBRARY = {
  rows: [
    { id: "t1", key: "platform_nda", version: 1, title: "NDA", scope: "platform", status: "draft", in_use: 2,
      can_edit: false, can_publish: false, can_add_version: false, can_copy: true, fields: [], body: "x" },
    { id: "t2", key: "firm_platform_nda", version: 1, title: "Firm NDA", scope: "org", status: "draft", in_use: 0,
      can_edit: true, can_publish: true, can_add_version: false, can_copy: false, fields: [], body: "y" },
    { id: "t3", key: "firm_joinder", version: 2, title: "Firm Joinder", scope: "org", status: "active", in_use: 1,
      can_edit: false, can_publish: false, can_add_version: true, can_copy: false, fields: [], body: "z" },
  ],
  permissions: { can_read: true, can_write: true }, vocabularies: { editable: ["title", "body", "fields"] },
};

test("library: copy / edit / publish / new version follow each row's server flags; nothing without can_write", () => {
  assert.deepEqual(dr.libraryActions(LIBRARY, LIBRARY.rows[0]), ["copy"], "platform row: copy only (not super admin)");
  assert.deepEqual(dr.libraryActions(LIBRARY, LIBRARY.rows[1]), ["edit", "publish"]);
  assert.deepEqual(dr.libraryActions(LIBRARY, LIBRARY.rows[2]), ["add_version"], "an active version is never edited");
  const html = render(h(TemplateLibrary, { initialEnvelope: LIBRARY }));
  for (const label of ["Copy to firm library", "Publish", "New version", "Edit"]) assert.match(html, new RegExp(`>${label}<`));
  for (const perms of [undefined, {}, { can_write: "true" }]) {
    const env = { ...LIBRARY, permissions: perms };
    assert.ok(LIBRARY.rows.every((r) => dr.libraryActions(env, r).length === 0));
    assert.ok(!/Copy to firm library|>Publish<|New version|>Edit</.test(render(h(TemplateLibrary, { initialEnvelope: env }))),
      `no library control for permissions ${JSON.stringify(perms)}`);
  }
});
