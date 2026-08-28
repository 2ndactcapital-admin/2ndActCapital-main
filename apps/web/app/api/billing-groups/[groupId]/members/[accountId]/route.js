import { forwardToApi } from "@/lib/apiForward";

// End a membership. The backend closes the row on both temporal axes rather
// than deleting it — a past invoice has to stay reproducible.
export async function DELETE(request, { params }) {
  const { groupId, accountId } = await params;
  return forwardToApi(
    `/api/v1/billing-groups/${encodeURIComponent(groupId)}` +
      `/members/${encodeURIComponent(accountId)}`,
    { method: "DELETE" },
  );
}
