import { forwardToApi } from "@/lib/apiForward";

export async function POST(request, { params }) {
  const { id } = await params;
  const body = await request.json();
  return forwardToApi(`/api/v1/spvs/${id}/subscriptions`, { method: "POST", body });
}
