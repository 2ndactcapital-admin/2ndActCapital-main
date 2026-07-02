import { forwardToApi } from "@/lib/apiForward";

export async function PATCH(request, { params }) {
  const { id, doc_id } = await params;
  const body = await request.json();
  return forwardToApi(`/api/v1/entities/${id}/documents/${doc_id}`, { method: "PATCH", body });
}
