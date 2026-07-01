import { forwardToApi } from "@/lib/apiForward";

export async function PATCH(request, { params }) {
  const { id } = await params;
  const body = await request.json();
  return forwardToApi(`/api/v1/entity-relationships/${id}`, { method: "PATCH", body });
}

export async function DELETE(request, { params }) {
  const { id } = await params;
  return forwardToApi(`/api/v1/entity-relationships/${id}`, { method: "DELETE" });
}
