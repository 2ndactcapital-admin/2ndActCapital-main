/**
 * Pure, dependency-free enrollment-flow logic.
 *
 * This module imports NOTHING — no React, no next/headers, no "@/..." aliases —
 * so the EXACT rules the shipped /enroll page uses can be exercised by a plain
 * Node harness (apps/api/scripts/enrollflow_harness.mjs). Same discipline as
 * lib/authHostConfig.mjs, and for the same reason: a test that greps the page
 * source for a string proves nothing about what the page decides. Here the
 * verify script calls these functions and reads the real return values.
 *
 * THE TENANT HAND-OFF, AND WHY IT NEEDS NO TENANT PLUMBING.
 * proxy.js already routes EVERY request through
 * `getAuthClientForHost(request.headers.get("host"))`, so the mounted
 * `/auth/login` route authenticates against whichever Auth0 tenant owns the
 * request's Host — 2ndactcapital.hollisworks.com → the 2nd Act tenant,
 * admin.hollisworks.com → the separate Hollisworks tenant (fail-loud). Because
 * the invite link is built from the issuing org's own `organizations.enroll_url`
 * (see services/invites.build_enrollment_url), the invitee is ALREADY on their
 * org's host by the time they reach /enroll. So the hand-off is a plain relative
 * redirect to /auth/login and the correct tenant follows from the host, with no
 * tenant identifier ever travelling in a URL where it could be tampered with.
 */

/** Query param the invite token travels in. Must match services/invites.TOKEN_PARAM. */
export const TOKEN_PARAM = "invite_token";

/** Where Auth0 returns the invitee after signup, to finalise the claim. */
export const COMPLETE_PATH = "/enroll/complete";

/** Statuses the backend's /enroll/validate can report. Mirrors services/invites. */
export const ENROLL_STATUS = {
  VALID: "valid",
  EXPIRED: "expired",
  ACCEPTED: "accepted",
  REVOKED: "revoked",
  NOT_FOUND: "not_found",
  MISSING: "missing_token",
  WRONG_TENANT: "wrong_tenant",
  SUB_CONFLICT: "sub_conflict",
  RACE: "race",
  UNREACHABLE: "unreachable",
};

/**
 * Headline + guidance + which action to offer, per status.
 *
 * Every non-valid state gets its OWN heading and its OWN next step. Expired and
 * already-accepted in particular must never render the same thing: one needs a
 * new invitation, the other just needs to sign in. `action` is what the page
 * renders as the primary control — `signup`, `login`, or `none`.
 */
const PRESENTATION = {
  [ENROLL_STATUS.VALID]: {
    title: "You have been invited",
    body:
      "Your invitation is valid. Create your account to finish enrolling — you will be asked to " +
      "choose a password, and your membership is set up already.",
    action: "signup",
    actionLabel: "Create your account",
    tone: "neutral",
  },
  [ENROLL_STATUS.EXPIRED]: {
    title: "This invitation has expired",
    body:
      "Invitations are valid for a limited time and this one has passed its expiry date. " +
      "Ask the person who invited you to send a new invitation — the link below will not work again.",
    action: "none",
    tone: "error",
  },
  [ENROLL_STATUS.ACCEPTED]: {
    title: "This invitation has already been used",
    body:
      "An account was already created from this invitation. Nothing is wrong — you simply need " +
      "to sign in rather than enrol again.",
    action: "login",
    actionLabel: "Sign in",
    tone: "notice",
  },
  [ENROLL_STATUS.REVOKED]: {
    title: "This invitation was withdrawn",
    body:
      "An administrator withdrew this invitation, so it can no longer be used. " +
      "Please contact them if you believe that was a mistake.",
    action: "none",
    tone: "error",
  },
  [ENROLL_STATUS.NOT_FOUND]: {
    title: "We do not recognise this invitation",
    body:
      "This link does not match any invitation. Check that you copied the whole link from your " +
      "invitation — links are long and are easily cut short — or ask for a new one.",
    action: "none",
    tone: "error",
  },
  [ENROLL_STATUS.MISSING]: {
    title: "This link is incomplete",
    body:
      "The invitation token is missing from this address. Please use the full link exactly as it " +
      "was sent to you.",
    action: "none",
    tone: "error",
  },
  [ENROLL_STATUS.WRONG_TENANT]: {
    title: "This invitation belongs to another firm",
    body:
      "You have opened it on a different firm's site, which cannot enrol you. Open the link from " +
      "your invitation directly — it points at the right place.",
    action: "none",
    tone: "error",
  },
  [ENROLL_STATUS.SUB_CONFLICT]: {
    title: "That account already belongs to someone here",
    body:
      "The account you signed in with is already linked to another member. Sign out and enrol " +
      "again using the address your invitation was sent to.",
    action: "none",
    tone: "error",
  },
  [ENROLL_STATUS.RACE]: {
    title: "This invitation has just been used",
    body:
      "It was claimed a moment ago — most likely by you, in another tab. Sign in to continue.",
    action: "login",
    actionLabel: "Sign in",
    tone: "notice",
  },
  [ENROLL_STATUS.UNREACHABLE]: {
    title: "We could not check your invitation",
    body:
      "Your invitation was not read because we could not reach the service — this is a problem on " +
      "our side, not with your link. Please try again in a moment.",
    action: "retry",
    actionLabel: "Try again",
    tone: "error",
  },
};

/**
 * Resolve the message to display for a validate response.
 *
 * `body` prefers the backend's own message when present, so the wording lives in
 * ONE place per state and the two halves can never drift into disagreeing about
 * what happened; the local copy is the fallback (and what an unreachable API
 * gets). An unknown status degrades to `not_found` — an honest "we don't
 * recognise this" — rather than an empty frame.
 */
export function enrollPresentation(result) {
  const status = result?.status && PRESENTATION[result.status]
    ? result.status
    : ENROLL_STATUS.NOT_FOUND;
  const preset = PRESENTATION[status];
  return {
    status,
    valid: status === ENROLL_STATUS.VALID,
    title: preset.title,
    body: (result?.message || preset.body || "").trim(),
    action: preset.action,
    actionLabel: preset.actionLabel || null,
    tone: preset.tone,
  };
}

/** Read the invite token out of a Next.js searchParams object. */
export function readInviteToken(searchParams) {
  const raw = searchParams?.[TOKEN_PARAM];
  const value = Array.isArray(raw) ? raw[0] : raw;
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

/**
 * The hand-off URL into the CORRECT tenant's Auth0 signup flow.
 *
 * Relative on purpose. `/auth/login` is mounted by the Auth0 SDK through
 * proxy.js, which selects the tenant from the request's own Host — so keeping
 * this relative is what makes the tenant correct, and also what makes it
 * un-tamperable (no tenant/domain travels in the query string).
 *
 *   screen_hint=signup  → Universal Login opens on Sign Up, not Log In. The SDK
 *                         forwards unrecognised query params to /authorize.
 *   login_hint=<email>  → pre-fills the invited address, so the account gets
 *                         created against the address the admin authorised.
 *   returnTo            → /enroll/complete?invite_token=…, which finalises the
 *                         claim after the callback. Relative, so the SDK's own
 *                         returnTo validation accepts it and it can never be
 *                         pointed off-site.
 */
export function buildSignupUrl({ email, inviteToken }) {
  if (!inviteToken) return null;
  const returnTo = `${COMPLETE_PATH}?${new URLSearchParams({
    [TOKEN_PARAM]: inviteToken,
  })}`;
  const params = new URLSearchParams({ screen_hint: "signup" });
  if (email) params.set("login_hint", email);
  params.set("returnTo", returnTo);
  return `/auth/login?${params}`;
}

/** Where an already-enrolled person should go instead. Relative for the same reason. */
export function buildLoginUrl() {
  return "/auth/login?returnTo=/dashboard";
}

/** The canonical /enroll link for a token — used to re-render the address honestly. */
export function buildEnrollPath(inviteToken) {
  if (!inviteToken) return "/enroll";
  return `/enroll?${new URLSearchParams({ [TOKEN_PARAM]: inviteToken })}`;
}
