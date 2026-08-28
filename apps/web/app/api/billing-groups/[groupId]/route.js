import { forwardToApi } from "@/lib/apiForward";

// One billing group. The id is scoped to the caller's org server-side, so a
// guessed id from another tenant reads as a 404, not a leak.
export async function PATCH(request, { params }) {
  const { groupId } = await params;
  const body = await request.json();
  return forwardToApi(
    `/api/v1/billing-groups/${encodeURIComponent(groupId)}`,
    { method: "PATCH", body },
  );
}

// Archives the group and closes its memberships — never a hard delete.
export async function DELETE(request, { params }) {
  const { groupId } = await params;
  return forwardToApi(
    `/api/v1/billing-groups/${encodeURIComponent(groupId)}`,
    { method: "DELETE" },
  );
}
