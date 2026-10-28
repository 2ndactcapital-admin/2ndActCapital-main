// DEALROOM3 — the REAL deal-room components rendered with react-dom/server
// from API-shaped envelopes (the shapes routers/deal_room.py returns): the
// view-only viewer with no download control, watermark / blocked-type flags,
// manager-only controls failing CLOSED without can_write, Q&A (Ask only with
// can_ask, no asker shown to investors), expired / paused investors, the
// advisor's acknowledgement-first view, the grid's access / advisors / extend /
// return-notice controls, and My deal access.
import assert from "node:assert/strict";
import { register } from "node:module";
import test from "node:test";

register("../market/jsxLoader.mjs", import.meta.url);

const { createElement: h } = await import("react");
const { renderToStaticMarkup } = await import("react-dom/server");
const dr = await import("../../lib/dealRoom.mjs");
const { default: DataRoom, FolderList } = await import("../../components/dealroom/DataRoom.jsx");
const { default: DocumentViewer } = await import("../../components/dealroom/DocumentViewer.jsx");
const { default: QAPanel } = await import("../../components/dealroom/QAPanel.jsx");
const { default: ActivityPanel } = await import("../../components/dealroom/ActivityPanel.jsx");
const { AdvisorAcknowledgement, InvestorAdvisors } = await import("../../components/dealroom/AdvisorsPanel.jsx");
const { default: AccessGrid } = await import("../../components/dealroom/AccessGrid.jsx");
const { default: MyDealAccess } = await import("../../components/dealroom/MyDealAccess.jsx");

const render = (el) => renderToStaticMarkup(el);
const text = (html) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&");

const VOCAB = {
  levels: [{ key: "public", label: "Public" }, { key: "after_agreements", label: "After agreements are signed" }],
  statuses: [{ key: "granted", label: "Granted" }, { key: "expired", label: "Access expired" },
    { key: "paused", label: "Paused — eligibility lapsed" }],
  eligibility_rules: [{ key: "accredited", label: "Accredited investor" }],
  approval_modes: [{ key: "automatic", label: "Automatic" }], sponsor_display: [{ key: "name", label: "Name" }],
  downloads: [{ key: "allowed", label: "Downloads allowed" }, { key: "view_only", label: "View only — no downloads" }],
  watermark: [{ key: "on", label: "Watermark every copy" }, { key: "off", label: "No watermark" }],
  access_periods: [{ key: "none", label: "No end date" }, { key: "until_date", label: "Until a date" }],
  notify_events: [{ key: "request", label: "A new access request" }, { key: "question", label: "A question is asked" }],
  offering_types: [{ key: "not_set", label: "Not set" }, { key: "rule_506b", label: "Rule 506(b) — DRAFT, pending counsel review" }],
  advisor_roles: [{ key: "lawyer", label: "Lawyer" }, { key: "accountant", label: "Accountant" }],
  resign_policies: [], master_scopes: [], agreement_kinds: [], editable: [],
};
const PDF = { id: "doc1", file_name: "om.pdf", file_type: "application/pdf", created_at: "2026-10-28T10:00:00Z" };
const DOCX = { id: "doc2", file_name: "model.xlsx", file_type: "application/vnd.ms-excel", created_at: "2026-10-28T10:00:00Z" };
const folder = (documents, open = true) => ({
  id: "f2", key: "02_offering_memorandum", label: "02 Offering Memorandum", sort_order: 2, access_level: "after_agreements",
  effective_level: "after_agreements", access_level_label: "After agreements are signed", parent_id: null,
  is_standard: true, open, lock_reason: open ? null : "Your access period for this deal has ended.",
  ...(open ? { documents } : {}),
});
const investorEnv = (over = {}) => ({
  deal: { id: "deal1", name: "Project Ember" }, rights: { role: "none", can_manage: false },
  permissions: { can_read: true, can_write: false }, vocabularies: VOCAB, agreements: [], investing_entities: [],
  my_access: { status: "granted", status_label: "Granted", access_ends_on: "2026-12-31" },
  room_rules: { downloads: "allowed", watermark: "on", offering_type: "not_set" },
  notices: { activity: "Opens, page views and downloads in this data room are recorded, and the publisher can see them." },
  folders: [folder([])], advisors: [], ...over,
});

test("view-only: no download control renders; View opens the page viewer; the notice says screenshots cannot be stopped", () => {
  const viewOnlyDoc = { ...PDF, can_view: true, can_download: false, watermarked: true, blocked_reason: null };
  assert.deepEqual(dr.documentActions(viewOnlyDoc), ["view"], "view-only → only View");
  assert.deepEqual(dr.documentActions({ ...viewOnlyDoc, can_download: "yes" }), ["view"], "only exactly true counts");
  const env = investorEnv({
    room_rules: { downloads: "view_only", watermark: "on" }, folders: [folder([viewOnlyDoc])],
    notices: { activity: "Opens are recorded.", view_only: "View-only deters copying but cannot stop screenshots or photographs." },
  });
  const html = render(h(FolderList, { envelope: env, manage: true, levels: [] }));
  assert.doesNotMatch(html, /data-action="download"/, "no download control under view-only");
  assert.match(html, /data-action="view"/);
  const viewer = render(h(DocumentViewer, { dealId: "deal1", info: { document_id: "doc1", file_name: "om.pdf",
    page_count: 3, watermarked: true }, viewOnlyNotice: env.notices.view_only }));
  assert.match(viewer, /\/api\/deal-room\/deals\/deal1\/documents\/doc1\/pages\/1/, "a page image from the API, not the file");
  assert.doesNotMatch(viewer, /download|r2\.|\.pdf"/i, "the viewer offers no download and no file URL");
  assert.match(text(viewer), /cannot stop screenshots/);
  assert.match(text(viewer), /watermarked with your name/);
  assert.deepEqual(dr.roomNotices(env), ["Opens are recorded.",
    "View-only deters copying but cannot stop screenshots or photographs."]);
});

test("watermark on: download renders only when can_download; a blocked type shows its reason", () => {
  const blocked = { ...DOCX, can_view: false, can_download: false,
    blocked_reason: "This file type cannot be watermarked — ask the publisher for a PDF." };
  const ok = { ...PDF, can_view: true, can_download: true, watermarked: true };
  const env = investorEnv({ folders: [folder([ok, blocked])] });
  const html = render(h(FolderList, { envelope: env, manage: true, levels: [] }));
  assert.equal((html.match(/data-action="download"/g) || []).length, 1, "one download control — the PDF's");
  assert.match(text(html), /cannot be watermarked — ask the publisher for a PDF/);
  assert.doesNotMatch(html, /share-unwatermarked/, "no manager control on an investor envelope");
  const room = render(h(DataRoom, { initialEnvelope: env }));
  assert.match(room, /data-testid="room-notices"/);
  assert.match(text(room), /Opens, page views and downloads in this data room are recorded/);
});

const managerEnv = (canWrite) => ({
  ...investorEnv({ rights: { role: "publisher", can_manage: canWrite } }),
  permissions: { can_read: true, can_write: canWrite },
  settings: { eligibility_rule: "accredited", approval_mode: "automatic", sponsor_display: "name", downloads: "allowed",
    watermark: "on", access_period: "none", expiry_notice_days: 7, notify_publisher_on: ["request"], offering_type: "rule_506b" },
  notices: { offering_draft: "The Rule 506(b) and 506(c) settings apply DRAFT rules that are pending counsel review." },
  folders: [folder([{ ...DOCX, can_view: false, can_download: true, watermarkable: false, share_unwatermarked: false }])],
  delegates: [], my_access: null,
});

test("manager controls (settings rows, share unwatermarked, activity, Q&A answer) only with can_write === true", () => {
  const on = managerEnv(true);
  const keys = dr.settingsRows(on).map((r) => r.key);
  for (const k of ["downloads", "watermark", "access_period", "offering_type"]) assert.ok(keys.includes(k), k);
  assert.equal(dr.shareUnwatermarkedControl(on, on.folders[0].documents[0]), true);
  const html = render(h(DataRoom, { initialEnvelope: on }));
  assert.match(html, /data-setting="watermark"/);
  assert.match(html, /data-setting="notify_publisher_on"/);
  assert.match(html, /data-action="share-unwatermarked"/);
  assert.match(html, /data-testid="activity-panel"/);
  assert.match(html, /data-testid="offering-draft"/, "the DRAFT offering rules are labelled");
  for (const env of [managerEnv(false), { ...managerEnv(true), permissions: undefined },
    { ...managerEnv(true), permissions: { can_write: "true" } }]) {
    assert.deepEqual(dr.settingsRows(env), []);
    assert.equal(dr.shareUnwatermarkedControl(env, env.folders[0].documents[0]), false);
    const out = render(h(DataRoom, { initialEnvelope: env }));
    assert.doesNotMatch(out, /data-setting=|share-unwatermarked|activity-panel|manager-panel/, "fails CLOSED");
    assert.equal(render(h(ActivityPanel, { envelope: env })), "");
  }
});

const QA = (perm, rows) => ({ rows, permissions: perm, vocabularies: { editable: perm.can_write === true ? ["answer", "withdraw", "merge"] : [] } });

test("Q&A: Ask only with can_ask; investors never see an asker's name; manager actions only with can_write", () => {
  const investorRows = [
    { id: "q1", body: "What is the hold period?", status: "answered", status_label: "Answered", answer: "Five years.", mine: false },
    { id: "q2", body: "Fees?", status: "open", status_label: "Open", mine: true },
  ];
  const inv = render(h(QAPanel, { dealId: "deal1", initial: QA({ can_write: false, can_ask: true }, investorRows) }));
  assert.match(inv, /data-testid="qa-ask"/);
  assert.doesNotMatch(inv, /asked by|qa-manager/, "no asker, no manager control");
  assert.match(text(inv), /Other investors see the answer, never who asked/);
  assert.match(text(inv), /your question/);
  const noAsk = render(h(QAPanel, { dealId: "deal1", initial: QA({ can_write: false, can_ask: false }, investorRows) }));
  assert.doesNotMatch(noAsk, /qa-ask/, "no Ask without can_ask === true");
  assert.deepEqual(dr.qaControls(QA({ can_ask: "true" }, [])), { ask: false, manage: [] });
  const mgr = render(h(QAPanel, { dealId: "deal1", initial: QA({ can_write: true, can_ask: false },
    [{ ...investorRows[1], mine: false, asker_name: "Jane Doe", asker_entity: "Doe LLC" }]) }));
  assert.match(mgr, /data-testid="qa-manager"/);
  assert.match(text(mgr), /asked by Jane Doe \(Doe LLC\)/, "managers see the asker");
});

test("expired / paused: no folders open, the reason shows, signatures stay", () => {
  const sig = [{ id: "a1", title: "NDA", sort_order: 1, signed_at: "2026-10-01T00:00:00Z", signature_id: "s1", signed_version: 1 }];
  const exp = investorEnv({ my_access: { status: "expired", status_label: "Access expired", access_ends_on: "2026-10-27" },
    folders: [folder([], false)], agreements: sig });
  assert.deepEqual(dr.investorStep(exp), { step: "expired", endedOn: "2026-10-27" });
  const html = render(h(DataRoom, { initialEnvelope: exp }));
  assert.match(html, /data-testid="expired-notice"/);
  assert.match(text(html), /NDA \(v1\) — signed 2026-10-01/, "the signature is still listed");
  assert.doesNotMatch(html, /data-action="(view|download)"/, "no document control");
  const paused = investorEnv({ my_access: { status: "paused", status_label: "Paused", paused_reason: "Accreditation lapsed." },
    folders: [folder([], false)] });
  assert.deepEqual(dr.investorStep(paused), { step: "paused", reason: "Accreditation lapsed." });
  assert.match(text(render(h(DataRoom, { initialEnvelope: paused }))), /paused: Accreditation lapsed\./);
});

test("advisor: acknowledgement first; no sign / request / ask / invite controls", () => {
  const adv = investorEnv({ rights: { role: "advisor", can_manage: false }, my_access: null, agreements: [], advisors: undefined,
    advisor: { status: "invited", needs_ack: true, investor_name: "Jane Doe", role_label: "Lawyer" },
    folders: [folder([], false)] });
  assert.equal(dr.investorStep(adv).step, "advisor_ack");
  const html = render(h(DataRoom, { initialEnvelope: adv }));
  assert.match(html, /data-testid="advisor-ack"/);
  assert.doesNotMatch(html, /sign-button|Request access|qa-ask|advisor-invite|investor-advisors/, "no investor control");
  const active = { ...adv, advisor: { ...adv.advisor, status: "active", needs_ack: false } };
  assert.equal(dr.investorStep(active).step, "advisor");
  assert.doesNotMatch(render(h(AdvisorAcknowledgement, { envelope: active })), /Acknowledge</);
  const inv = render(h(InvestorAdvisors, { envelope: investorEnv({ advisors: [{ advisor_user_id: "u9", name: "Lee Counsel",
    firm: "Counsel LLP", role: "lawyer", role_label: "Lawyer", status: "active", status_label: "Active" }] }) }));
  assert.match(inv, /data-testid="advisor-invite"/, "a granted investor may invite");
  assert.match(text(inv), /Lee Counsel, Counsel LLP — Lawyer · Active/);
  const notGranted = render(h(InvestorAdvisors, { envelope: investorEnv({ my_access: { status: "expired" } }) }));
  assert.doesNotMatch(notGranted, /advisor-invite/, "no invite once access is not granted");
});

const GRID = {
  rows: [{ id: "acc1", deal_id: "d1", deal_name: "Project Ember", investor_name: "Jane Doe", entity_name: "Doe LLC",
    eligibility: {}, agreements: {}, agreement_detail: {}, status: "granted", status_label: "Granted", group: "Project Ember",
    access_ends_on: "2026-12-31", paused_reason: null,
    advisors: [{ advisor_user_id: "u9", name: "Lee Counsel", firm: null, role: "lawyer", role_label: "Lawyer", status: "active", status_label: "Active" }],
    return_notice: { sent_at: "2026-10-28T10:00:00Z", acknowledged_at: null } }],
  total: 1, page: 0, page_size: 50, group_by: "deal", agreement_columns: [],
  permissions: { can_read: true, can_write: true },
  vocabularies: { ...VOCAB, actions: ["approve", "decline", "revoke", "require_resign", "extend"],
    extendable_statuses: ["granted", "expired"], group_by: ["deal"], grant_levels: VOCAB.levels.slice(1) },
};

test("grid: access line, advisors under the investor, Extend + return-or-destroy only from the envelope's actions", () => {
  assert.equal(dr.accessLine(GRID.rows[0]), "Granted · until 2026-12-31");
  assert.equal(dr.accessLine({ status: "paused", status_label: "Paused", paused_reason: "KYC lapsed." }), "Paused · KYC lapsed.");
  assert.deepEqual(dr.rowActions(GRID, { status: "revoked" }).includes("extend"), false);
  assert.deepEqual(dr.rowActions(GRID, { status: "expired" }).includes("extend"), true);
  const html = render(h(AccessGrid, { initialEnvelope: GRID }));
  assert.match(text(html), /Extend/);
  assert.match(text(html), /Send return-or-destroy notice/);
  const off = render(h(AccessGrid, { initialEnvelope: { ...GRID, permissions: { can_read: true, can_write: false } } }));
  assert.doesNotMatch(off, /grid-actions|Extend|return-or-destroy notice</, "no action without can_write");
  assert.deepEqual(dr.advisorLines(GRID.rows[0].advisors), ["Lee Counsel — Lawyer · Active"]);
});

test("my deal access: end date, paused reason, advisors, return-or-destroy notice, deals I advise on", () => {
  const html = render(h(MyDealAccess, {
    rows: [{ deal_id: "d1", deal_name: "Project Ember", status: "revoked", status_label: "Revoked", entity_name: "Doe LLC",
      access_ends_on: null, paused_reason: null, to_sign: [], signatures: [],
      advisors: [{ advisor_user_id: "u9", name: "Lee Counsel", role_label: "Lawyer", status: "revoked", status_label: "Removed" }],
      return_notice: { text: "Return-or-destroy notice — Project Ember", sent_at: "2026-10-28T10:00:00Z", acknowledged_at: null } },
    { deal_id: "d2", deal_name: "Project Flint", status: "granted", status_label: "Granted", access_ends_on: "2026-11-30",
      to_sign: [], signatures: [], advisors: [], return_notice: null }],
    advising: [{ deal_id: "d3", deal_name: "Project Gale", investor_name: "Ann Lee", role_label: "Accountant",
      status: "invited", status_label: "Invited", needs_ack: true }],
  }));
  const t = text(html);
  assert.match(html, /data-testid="return-notice"/);
  assert.match(t, /Return-or-destroy notice — Project Ember/);
  assert.match(t, /Advisor: Lee Counsel — Lawyer · Removed/);
  assert.match(t, /Granted · until 2026-11-30/);
  assert.match(html, /data-testid="advising"/);
  assert.match(t, /Project Gale/);
  assert.match(t, /acknowledgement needed/);
});
