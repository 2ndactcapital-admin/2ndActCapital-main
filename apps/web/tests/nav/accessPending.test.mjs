// AUTHZ1 — the pending page renders from a /users/me payload with status
// 'pending', and the menu rule fails closed for a role-less / unadmitted payload.
import assert from "node:assert/strict";
import { register } from "node:module";
import test from "node:test";

process.env.NAV_TEST_STUBS = "1";
register("../market/jsxLoader.mjs", import.meta.url);
register("./navLoader.mjs", import.meta.url);

const { createElement: h } = await import("react");
const { renderToStaticMarkup } = await import("react-dom/server");
const AccessPendingModule = await import("../../components/AccessPending.jsx");
const { default: AccessPending, isAccessPending } = AccessPendingModule;
const menu = await import("../../lib/menuVisibility.mjs");

test("pending page renders for status 'pending'", () => {
  const me = { status: "pending", email: "someone@example.com", roles: [], permissions: [] };
  assert.equal(isAccessPending(me), true);
  const html = renderToStaticMarkup(h(AccessPending, { me }));
  assert.ok(html.includes('data-access-status="pending"'));
  assert.ok(html.includes("Access pending — an administrator has been notified."));
});

test("an active payload is not pending; a missing payload is not pending either (server still gates)", () => {
  assert.equal(isAccessPending({ status: "active" }), false);
  assert.equal(isAccessPending(null), false);
});

test("canPerm has no default-allow: a role-less payload sees only always-visible items", () => {
  const me = { account_role: "member", roles: [], permissions: [], status: "active" };
  const visible = menu.visibleMenuItems(me).map((i) => i.href);
  // BUGFIX1: comingSoon items are never listed, for anyone.
  const ungated = menu.MENU_ITEMS.filter((i) => !i.gate && !i.comingSoon).map((i) => i.href);
  assert.deepEqual(visible, ungated);
  assert.equal(menu.canPerm(me, "manage_members"), false);
});

test("a pending payload is granted nothing, even holding a permission string", () => {
  const me = { account_role: "super_admin", roles: ["admin"], permissions: ["manage_members"], status: "pending" };
  assert.equal(menu.canPerm(me, "manage_members"), false);
  assert.equal(menu.canPermStrict(me, "manage_members"), false);
});

test("Super Admin first; granted permission passes", () => {
  assert.equal(menu.canPerm({ account_role: "super_admin", roles: [], permissions: [] }, "manage_members"), true);
  assert.equal(menu.canPerm({ account_role: "member", roles: ["m"], permissions: ["manage_members"] }, "manage_members"), true);
});

// USERMGMT1 — the pending page explains the wait and asks for an email only
// when none real is on file; what is typed goes to contact_email (unverified).
test("pending with a placeholder email asks for a contact address", () => {
  const me = { status: "pending", email: "auth0|x@placeholder.local", email_is_placeholder: true, contact_email: null };
  const { needsContactEmail } = AccessPendingModule;
  assert.equal(needsContactEmail(me), true);
  const html = renderToStaticMarkup(h(AccessPending, { me }));
  assert.ok(html.includes("data-contact-form"));
  assert.ok(html.includes("We do not have an email address for this sign-in."));
  assert.ok(html.includes("did not match a member account here"));
  assert.equal(html.includes("Signed in as"), false);
});

test("pending with a real email, or a contact address already left, does not ask", () => {
  const { needsContactEmail } = AccessPendingModule;
  const real = { status: "pending", email: "someone@example.com", email_is_placeholder: false };
  assert.equal(needsContactEmail(real), false);
  assert.equal(renderToStaticMarkup(h(AccessPending, { me: real })).includes("data-contact-form"), false);
  const left = { status: "pending", email: "auth0|x@placeholder.local", email_is_placeholder: true, contact_email: "c@example.com" };
  assert.equal(needsContactEmail(left), false);
  const html = renderToStaticMarkup(h(AccessPending, { me: left }));
  assert.ok(html.includes("c@example.com") && !html.includes("data-contact-form"));
  assert.equal(needsContactEmail({ status: "active", email_is_placeholder: true }), false);
});
