import { forwardToApi } from "@/lib/apiForward";

export async function PUT(request, { params }) {
  const { id } = await params;
  return forwardToApi(`/api/v1/notifications/${id}/read`, { method: "PUT" });
}
