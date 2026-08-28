import { forwardToApi } from "@/lib/apiForward";

// A group's active members, plus the assignable-account list with each
// account's blocker attached. A blocked account comes back WITH its blocker
// rather than filtered out, so the picker can grey it out and say why.
export async function GET(request, { params }) {
  const { groupId } = await params;
  return forwardToApi(
    `/api/v1/billing-groups/${encodeURIComponent(groupId)}/members`,
  );
}

// Adding an account to a BREAKPOINT group it may not join comes back as a 409
// carrying both group ids — forwarded through verbatim so the screen can name
// the blocking group rather than showing a generic failure.
export async function POST(request, { params }) {
  const { groupId } = await params;
  const body = await request.json();
  return forwardToApi(
    `/api/v1/billing-groups/${encodeURIComponent(groupId)}/members`,
    { method: "POST", body },
  );
}
