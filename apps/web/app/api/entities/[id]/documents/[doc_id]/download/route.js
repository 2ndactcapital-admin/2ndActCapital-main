import { forwardToApi } from "@/lib/apiForward";

export async function GET(request, { params }) {
  const { id, doc_id } = await params;
  return forwardToApi(`/api/v1/entities/${id}/documents/${doc_id}/download`);
}
