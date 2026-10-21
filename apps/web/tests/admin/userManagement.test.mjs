// USERMGMT1 — People & Access: the REAL row / pending / sign-in components
// rendered with react-dom/server from GET /admin/users-shaped rows. Every
// control must come from the row's server-published `actions`; a view-only
// row (actions: []) and a row with no `actions` field render none.
import assert from "node:assert/strict";
import { register } from "node:module";
import test from "node:test";

register("../market/jsxLoader.mjs", import.meta.url);

const { createElement: h } = await import("react");
const { renderToStaticMarkup } = await import("react-dom/server");
const parts = await import("../../components/admin/PeopleParts.jsx");
const M = await import("../../lib/userManagementModel.mjs");

const ROLE_A = { id: "r-1", name: "admin" };
const ROLE_B = { id: "r-2", name: "org_admin" };
const DB_LOGIN = { tenant: "2nd Act", method: "Email & password", is_database: true, linked_via: "primary" };
const GOOGLE_LINK = { tenant: "2nd Act", method: "Google", is_database: false, linked_via: "duplicate_merge" };

const ALL_ACTIONS = ["edit", "reset_password", "edit_email", "retire"];

function row(over = {}) {
  return {
    id: "u-1", email: "person@example.com", full_name: "Person One", status: "active", is_active: true,
    roles: [ROLE_A], sign_in: [DB_LOGIN], never_signed_in: false, email_is_placeholder: false,
    is_test: false, is_deleted: false, invite_status: null, actions: [], reset_password_refusal: null,
    ...over,
  };
}

const render = (el) => renderToStaticMarkup(el);
const table = (child) => h("table", null, h("tbody", null, child));
const noop = () => {};

test("one row per person: two roles render as two chips on ONE row", () => {
  const html = render(table(h(parts.UserRow, { user: row({ roles: [ROLE_A, ROLE_B] }), onEdit: noop })));
  assert.equal((html.match(/data-user-row=/g) || []).length, 1);
  assert.ok(html.includes('data-roles="2"'));
  assert.ok(html.includes("Admin") && html.includes("Org Admin"));
});

test("sign-in column: primary database login, then a linked Google login", () => {
  const html = render(h(parts.SignInCell, { user: row({ sign_in: [DB_LOGIN, GOOGLE_LINK] }) }));
  assert.ok(html.includes("2nd Act · Email &amp; password"));
  assert.ok(html.includes("2nd Act · Google (linked)"));
  assert.deepEqual(M.signInLines(row({ sign_in: [] })), ["Never signed in"]);
});

test("a view-only row (actions: []) renders no control at all", () => {
  const html = render(table(h(parts.UserRow, { user: row({ actions: [] }), onEdit: noop, onRevoke: noop })));
  assert.equal(html.includes("data-action="), false);
  const signIn = render(h(parts.SignInActions, { user: row({ actions: [] }) }));
  assert.equal(signIn, "");
});

test("a row with NO actions field fails closed (no controls)", () => {
  const u = row();
  delete u.actions;
  assert.equal(M.can(u, "edit"), false);
  const html = render(table(h(parts.UserRow, { user: u, onEdit: noop, onRevoke: noop })));
  assert.equal(html.includes("data-action="), false);
});

test("a managing caller sees Manage; Revoke invite only for a never-signed-in invite", () => {
  let html = render(table(h(parts.UserRow, { user: row({ actions: ALL_ACTIONS }), onEdit: noop, onRevoke: noop })));
  assert.ok(html.includes('data-action="edit"'));
  assert.equal(html.includes('data-action="revoke-invite"'), false);
  html = render(table(h(parts.UserRow, {
    user: row({ actions: ALL_ACTIONS, never_signed_in: true, sign_in: [], invite_status: "pending" }),
    onEdit: noop, onRevoke: noop,
  })));
  assert.ok(html.includes('data-action="revoke-invite"'));
});

test("password reset: button for a database login; the server's refusal text otherwise", () => {
  let html = render(h(parts.SignInActions, { user: row({ actions: ["reset_password"] }) }));
  assert.ok(html.includes('data-action="reset-password"'));
  const refusal = "This account signs in with Google — reset the password in Google.";
  html = render(h(parts.SignInActions, {
    user: row({ actions: ["reset_password"], sign_in: [GOOGLE_LINK], reset_password_refusal: refusal }),
  }));
  assert.equal(html.includes('data-action="reset-password"'), false);
  assert.ok(html.includes("reset the password in Google"));
});

test("resend invite and edit email render only when named, with the Auth0 note", () => {
  const user = row({
    actions: ["resend_invite", "edit_email"], never_signed_in: true, sign_in: [],
    email: "auth0|x@placeholder.local", email_is_placeholder: true,
  });
  const html = render(h(parts.SignInActions, { user }));
  assert.ok(html.includes('data-action="resend-invite"'));
  assert.ok(html.includes('data-action="save-email"'));
  assert.ok(html.includes("It does not change the email the person signs in with."));
  const withLink = render(h(parts.SignInActions, {
    user, result: { enrollment_url: "https://x.example/enroll?invite_token=SENTINEL", email_delivery: { sent: false } },
  }));
  assert.ok(withLink.includes("invite_token=SENTINEL") && withLink.includes("share this link"));
});

test("pending panel: explains pending, suggests a likely match, controls per actions", () => {
  const pending = row({
    id: "p-1", status: "pending", email: "auth0|p@placeholder.local", email_is_placeholder: true,
    full_name: "person@example.com", contact_email: "typed@example.com",
    likely_match: { id: "u-1", email: "person@example.com", full_name: "Person One" },
    actions: ["approve", "reject", "link"],
  });
  const target = row({ id: "u-1" });
  const html = render(h(parts.PendingPanel, {
    pending: [pending], users: [pending, target], roles: [ROLE_A], onApprove: noop, onReject: noop, onLink: noop,
  }));
  assert.ok(html.includes("did not match an existing member"));
  assert.ok(html.includes('data-likely-match="u-1"') && html.includes("Looks like Person One"));
  assert.ok(html.includes("typed@example.com (unverified)"));
  for (const a of ["approve", "reject", "link"]) assert.ok(html.includes(`data-action="${a}"`), a);
  const viewOnly = render(h(parts.PendingPanel, {
    pending: [{ ...pending, actions: ["approve", "reject"] }], users: [target], roles: [ROLE_A],
    onApprove: noop, onReject: noop, onLink: noop,
  }));
  assert.equal(viewOnly.includes('data-action="link"'), false);
});

test("list params: test and retired hidden by default, sent only when asked", () => {
  const d = M.listParams({});
  assert.equal(d.include_test, undefined);
  assert.equal(d.include_retired, undefined);
  const s = M.listParams({ showTest: true, showRetired: true, status: "pending" });
  assert.equal(s.include_test, "true");
  assert.equal(s.include_retired, "true");
  assert.equal(s.status, "pending");
});

test("status labels and link candidates", () => {
  assert.equal(M.statusLabel(row({ status: "pending" })), "Awaiting approval");
  assert.equal(M.statusLabel(row({ status: "retired" })), "Retired");
  assert.equal(M.statusLabel(row({ is_active: false })), "Deactivated");
  assert.equal(M.statusLabel(row({ never_signed_in: true, invite_status: "pending", sign_in: [] })), "Invited");
  assert.equal(M.statusLabel(row()), "Active");
  const cands = M.linkCandidates([row({ id: "a" }), row({ id: "b", is_test: true }), row({ id: "c", status: "pending" })],
    { id: "p" });
  assert.deepEqual(cands.map((c) => c.id), ["a"]);
});
