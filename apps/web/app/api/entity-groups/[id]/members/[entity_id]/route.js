import { forwardToApi } from "@/lib/apiForward";

export async function DELETE(request, { params }) {
  const { id, entity_id } = await params;
  return forwardToApi(`/api/v1/entity-groups/${id}/members/${entity_id}`, { method: "DELETE" });
}
