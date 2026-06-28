import { forwardToApi } from "@/lib/apiForward";

export async function POST(request, { params }) {
  const { id } = await params;
  return forwardToApi(`/api/v1/assistant/activity/${id}/undo`, { method: "POST" });
}
