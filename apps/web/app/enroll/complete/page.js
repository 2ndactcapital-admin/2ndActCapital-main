import { headers } from "next/headers";
import { redirect } from "next/navigation";

import { acceptInvite } from "@/lib/api";
import { getHostSession } from "@/lib/authServer";
import {
  ENROLL_STATUS,
  buildEnrollPath,
  buildLoginUrl,
  enrollPresentation,
  readInviteToken,
} from "@/lib/enrollFlow";
import { brandName } from "@/lib/theme";
import { loadTheme } from "@/lib/themeServer";
import EnrollShell from "@/components/EnrollShell";

/**
 * /enroll/complete — where Auth0 returns the invitee after signup.
 *
 * This is the step that makes enrollment real: it links the Auth0 identity that
 * was just created to the PENDING users row the admin provisioned, and flips
 * invite_status to 'accepted'.
 *
 * MATCH, DON'T DUPLICATE. The backend claims the existing row by invite token
 * rather than letting ensure_user insert a fresh one — see routers/enroll.py.
 * That matters because users.email is UNIQUE and the pending row already carries
 * the org, role and address the admin authorised; a second row would either
 * collide outright or leave the member with none of it.
 *
 * The session is read with getHostSession() — the HOST-AWARE client — so an
 * invitee who enrolled on admin.hollisworks.com is read out of the Hollisworks
 * tenant's own cookie rather than 2nd Act's, which is the redirect-loop bug
 * fixed earlier this session.
 */

export async function generateMetadata() {
  const theme = await loadTheme();
  const name = brandName(theme.settings || {});
  return { title: name ? `Enrol — ${name}` : "Enrol" };
}

export default async function EnrollCompletePage({ searchParams }) {
  const params = await searchParams;
  const host = (await headers()).get("host") || "";
  const inviteToken = readInviteToken(params);
  const theme = await loadTheme();
  const platform = brandName(theme.settings || {});

  if (!inviteToken) {
    const view = enrollPresentation({ status: ENROLL_STATUS.MISSING });
    return <EnrollShell {...view} orgName={platform} />;
  }

  // No session means Auth0 has not actually completed. Send them back to the
  // start of their own invitation rather than showing a half-finished state.
  const session = await getHostSession();
  if (!session) redirect(buildEnrollPath(inviteToken));

  const result = await acceptInvite({ inviteToken, host });

  // Claim succeeded — the pending row now carries this auth0_sub and reads
  // 'accepted'. Straight into the app; there is nothing useful to pause on.
  // (redirect() throws NEXT_REDIRECT to unwind, which is why acceptInvite is
  // resolved BEFORE this line rather than inside a try that would swallow it.)
  if (result?.ok && result?.status === ENROLL_STATUS.VALID) redirect("/dashboard");

  // Anything else: the backend sent the exact, specific status AND sentence for
  // this outcome, so render those rather than inferring a reason from a code.
  const view = enrollPresentation(result);
  return (
    <EnrollShell
      title={view.title}
      body={view.body}
      tone={view.tone}
      orgName={result?.org_name || platform}
      actionHref={
        view.action === "login" ? result?.login_url || buildLoginUrl() : null
      }
      actionLabel={view.actionLabel}
    />
  );
}
