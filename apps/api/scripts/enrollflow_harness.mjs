/**
 * Hermetic Node harness over apps/web/lib/enrollFlow.mjs.
 *
 * It imports the EXACT module the shipped /enroll and /enroll/complete pages
 * import — nothing about the message selection or the Auth0 hand-off is
 * re-implemented here. Prints one JSON object to stdout; verify_enrollurl.py
 * reads it and asserts on the real return values.
 *
 * Why a harness rather than grepping the page source: a grep proves a string is
 * present, not that the page chooses it. The whole point of the expired-vs-used
 * distinction is which branch runs, so the branch has to actually run.
 */

import {
  ENROLL_STATUS,
  TOKEN_PARAM,
  COMPLETE_PATH,
  buildEnrollPath,
  buildLoginUrl,
  buildSignupUrl,
  enrollPresentation,
  readInviteToken,
} from "../../web/lib/enrollFlow.mjs";

const out = {};

// 1. Every status the backend can report renders its own distinct presentation.
out.presentations = {};
for (const status of Object.values(ENROLL_STATUS)) {
  out.presentations[status] = enrollPresentation({ status });
}

// 2. The backend's own message wins when supplied (single source of wording).
out.backend_message_wins = enrollPresentation({
  status: ENROLL_STATUS.EXPIRED,
  message: "BACKEND SENTENCE",
}).body;

// 3. An unknown/garbage status degrades honestly rather than rendering blank.
out.unknown_status = enrollPresentation({ status: "wat" });
out.null_result = enrollPresentation(null);

// 4. Token reading, including the array form Next can hand back.
out.token_plain = readInviteToken({ [TOKEN_PARAM]: "tok-abc" });
out.token_array = readInviteToken({ [TOKEN_PARAM]: ["tok-abc", "tok-def"] });
out.token_blank = readInviteToken({ [TOKEN_PARAM]: "   " });
out.token_absent = readInviteToken({});
out.token_undefined_params = readInviteToken(undefined);

// 5. The Auth0 hand-off URL.
out.signup_url = buildSignupUrl({
  email: "invitee@example.com",
  inviteToken: "tok abc/+=",
});
out.signup_url_no_email = buildSignupUrl({ inviteToken: "tok-abc" });
out.signup_url_no_token = buildSignupUrl({ email: "a@b.c", inviteToken: null });
out.login_url = buildLoginUrl();
out.enroll_path = buildEnrollPath("tok abc/+=");
out.complete_path = COMPLETE_PATH;

process.stdout.write(JSON.stringify(out, null, 2));
