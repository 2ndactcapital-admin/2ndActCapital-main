import { forwardToApi } from "@/lib/apiForward";

export async function PATCH(request, { params }) {
  const { id } = await params;
  const body = await request.json();
  return forwardToApi(`/api/v1/dashboard/todos/${id}`, { method: "PATCH", body });
}
