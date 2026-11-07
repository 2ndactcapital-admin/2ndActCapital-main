// VISIBILITY1 — the staff-visibility screens render write controls ONLY from the server's envelope and fail closed.
// Envelopes are written by hand in the API's shapes (routers/staff_visibility.py), never imported from it.
import assert from "node:assert/strict";
import { register } from "node:module";
import test from "node:test";

register("../market/jsxLoader.mjs", import.meta.url);

const { createElement: h } = await import("react");
const { renderToStaticMarkup } = await import("react-dom/server");
const V = await import("../../lib/visibility1.mjs");
const { TeamView } = await import("../../components/visibility/TeamPanel.jsx");
const { PreviewView, UnassignedView, PeopleView } = await import("../../components/visibility/StaffVisibilityAdmin.jsx");

const VOCAB = { role_labels: ["Lead adviser", "Associate", "Service"],
  reason_labels: { assignment: "Assigned to them", team: "Assigned to their team", report: "Assigned to someone who reports to them",
    relationship_manager: "Relationship manager", creator: "Created it (no one is assigned)", all_org_admin: "Org admin — every family in the org" } };
const TEAM = {
  family: { key: "household:h1", kind: "household", id: "h1", name: "Olivo Family", entity_count: 4 },
  owner: { created_by: [{ user_id: "u2", name: "Sam Second" }], relationship_managers: [] },
  assignments: [{ id: "a1", entity_id: "e1", entity_name: "Marco Olivo", assigned_to_user_id: "u1",
    assigned_to_user_name: "Sara First", assigned_to_team_id: null, role_label: "Lead adviser" }],
  shared_record: false,
};
const writer = { ...TEAM, permissions: { can_read: true, can_write: true }, vocabularies: { ...VOCAB, editable: ["assignments"], inline_editable: [] } };
const viewer = { ...TEAM, permissions: { can_read: true, can_write: false }, vocabularies: { ...VOCAB, editable: [], inline_editable: [] } };
const people = [{ user_id: "u1", full_name: "Sara First" }];

test("Team panel: an assigner sees Assign and Remove", () => {
  const html = renderToStaticMarkup(h(TeamView, { envelope: writer, people, teams: [] }));
  assert.match(html, /Sara First/);
  assert.match(html, /Lead adviser/);
  assert.match(html, />Assign</);
  assert.match(html, />Remove</);
  assert.match(html, /Created by: Sam Second/);
});

test("Team panel: a view-only envelope renders no write control", () => {
  const html = renderToStaticMarkup(h(TeamView, { envelope: viewer, people, teams: [] }));
  assert.match(html, /Sara First/);
  assert.doesNotMatch(html, />Assign</);
  assert.doesNotMatch(html, />Remove</);
});

test("Team panel: a lost permissions block fails closed", () => {
  const { permissions, ...noPerms } = writer;
  assert.equal(V.canWrite(noPerms), false);
  assert.deepEqual(V.teamControls(noPerms), { add: false, remove: false });
  assert.deepEqual(V.teamControls({ ...writer, vocabularies: undefined }), { add: false, remove: false });
  const html = renderToStaticMarkup(h(TeamView, { envelope: noPerms, people, teams: [] }));
  assert.doesNotMatch(html, />Assign</);
  assert.equal(renderToStaticMarkup(h(TeamView, { envelope: null })), "");
});

const PREVIEW = {
  enforced: false, families_total: 3, unassigned_count: 1,
  people: [
    { user_id: "oa", name: "Olive Admin", sees_all: true, family_count: 3, families: [] },
    { user_id: "s1", name: "Sara First", sees_all: false, family_count: 2,
      families: [{ key: "household:h1", name: "F1", reasons: [{ kind: "assignment", label: "Assigned to them" }] },
        { key: "household:h2", name: "F2", reasons: [{ kind: "team", team: "Team T", label: "Assigned to their team" }] }] },
    { user_id: "s3", name: "Nobody Assigned", sees_all: false, family_count: 0, families: [] },
  ],
  sees_none: [{ user_id: "s3", name: "Nobody Assigned" }],
  permissions: { can_read: true, can_write: true }, vocabularies: { ...VOCAB, editable: ["enforced"], inline_editable: [] },
};

test("Preview: counts, the sees-none warning and the switch for an org admin", () => {
  const html = renderToStaticMarkup(h(PreviewView, { preview: PREVIEW }));
  assert.match(html, /Enforcement is <strong[^>]*>off</);
  assert.match(html, /Would see no family: Nobody Assigned/);
  assert.match(html, /Turn enforcement on/);
  assert.deepEqual(V.previewRows(PREVIEW).map((r) => r.count), ["All", "2", "0"]);
  assert.equal(V.reasonText(PREVIEW.people[1].families[1].reasons[0], VOCAB), "Assigned to their team (Team T)");
});

test("Preview: no switch without the envelope's grant", () => {
  const html = renderToStaticMarkup(h(PreviewView, { preview: { ...PREVIEW, permissions: { can_read: true, can_write: false } } }));
  assert.doesNotMatch(html, /Turn enforcement/);
  assert.equal(V.switchControl({ ...PREVIEW, vocabularies: { editable: [] } }), false);
});

test("Confirm step states the preview's own numbers", () => {
  const s = V.confirmSummary(PREVIEW, true);
  assert.match(s, /1 of 3 families have no one assigned/);
  assert.match(s, /1 staff member\(s\) will see no family/);
  assert.match(V.confirmSummary(PREVIEW, false), /again see every family/);
});

test("Unassigned families: bulk assign only for an assigner envelope", () => {
  const env = { rows: [{ key: "household:h3", name: "F3", entity_count: 2, created_by: [{ user_id: "s2", name: "Sam Second" }], relationship_managers: [] }],
    permissions: { can_read: true, can_write: true }, vocabularies: { ...VOCAB, editable: ["assignments"] } };
  const html = renderToStaticMarkup(h(UnassignedView, { envelope: env, people }));
  assert.match(html, /F3/);
  assert.match(html, /Sam Second/);
  assert.match(html, /type="checkbox"/);
  assert.match(html, /selected<\/button>/);
  const ro = renderToStaticMarkup(h(UnassignedView, { envelope: { ...env, permissions: { can_write: false } }, people }));
  assert.doesNotMatch(ro, /type="checkbox"/);
  assert.doesNotMatch(ro, /selected<\/button>/);
});

test("People: the manager selector only when the server admitted the people list", () => {
  const p = [{ user_id: "s1", full_name: "Sara First", manager_id: "m", manager_name: "Max Manager", teams: ["Team T"] },
    { user_id: "m", full_name: "Max Manager", manager_id: null, teams: [] }];
  assert.match(renderToStaticMarkup(h(PeopleView, { people: p, canEdit: true })), /<select/);
  const ro = renderToStaticMarkup(h(PeopleView, { people: p, canEdit: false }));
  assert.doesNotMatch(ro, /<select/);
  assert.match(ro, /Max Manager/);
});
