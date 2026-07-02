import { forwardToApi } from "@/lib/apiForward";

export async function GET(request, { params }) {
  const { id } = await params;
  return forwardToApi(`/api/v1/entities/${id}/ownership/history`);
}
