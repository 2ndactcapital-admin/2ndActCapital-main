import { forwardToApi } from "@/lib/apiForward";

export async function PATCH(request, { params }) {
  const { id, sub_id } = await params;
  const body = await request.json();
  return forwardToApi(`/api/v1/spvs/${id}/subscriptions/${sub_id}`, {
    method: "PATCH",
    body,
  });
}
