import { headers } from "next/headers";

import { validateInvite } from "@/lib/api";
import {
  ENROLL_STATUS,
  buildLoginUrl,
  buildSignupUrl,
  enrollPresentation,
  readInviteToken,
} from "@/lib/enrollFlow";
import { brandName } from "@/lib/theme";
import { loadTheme } from "@/lib/themeServer";
import EnrollShell from "@/components/EnrollShell";

/**
 * /enroll — the page an invited member lands on.
 *
 * This route did not exist before this sprint. The invite backend has shipped
 * since Multi-tenant Sprint 2 and the admin UI can mint tokens, but the link
 * they produced was (a) a bare relative path and (b) pointed at a 404. Both
 * halves are fixed together, because either alone still leaves the flow broken.
 *
 * A server component on purpose: the token is checked against the database
 * BEFORE anything renders, so an expired or spent invitation never gets as far
 * as showing a "Create your account" button that would fail at the end.
 *
 * The Auth0 hand-off is a relative link to /auth/login — proxy.js picks the
 * tenant from this request's own Host, and the invitee is already on their org's
 * host because the link was built from that org's enroll_url. See lib/enrollFlow.
 */

export async function generateMetadata() {
  const theme = await loadTheme();
  const name = brandName(theme.settings || {});
  return { title: name ? `Enrol — ${name}` : "Enrol" };
}

export default async function EnrollPage({ searchParams }) {
  const params = await searchParams;
  const host = (await headers()).get("host") || "";
  const inviteToken = readInviteToken(params);

  // No token at all: don't call the API, and don't render a generic error —
  // "your link is incomplete" is the true and useful thing to say.
  const result = inviteToken
    ? await validateInvite({ inviteToken, host })
    : { status: ENROLL_STATUS.MISSING };

  const view = enrollPresentation(result);
  const theme = await loadTheme();
  const platform = brandName(theme.settings || {});
  // The org named on the invitation, when we know it — it is more meaningful to
  // an invitee than the platform's own brand, and it also lets them notice
  // immediately if the invitation is not the one they expected.
  const orgName = result?.org_name || platform;

  const href =
    view.action === "signup"
      ? buildSignupUrl({ email: result?.email, inviteToken })
      : view.action === "login"
        ? result?.login_url || buildLoginUrl()
        : null;

  return (
    <EnrollShell
      title={view.title}
      body={view.body}
      tone={view.tone}
      orgName={orgName}
      email={view.valid ? result?.email : null}
      actionHref={href}
      actionLabel={view.actionLabel}
      footnote={
        view.status === ENROLL_STATUS.WRONG_TENANT && result?.correct_url
          ? `Your invitation belongs to ${result.org_name || "another firm"} — open it at ${result.correct_url}.`
          : null
      }
    />
  );
}
