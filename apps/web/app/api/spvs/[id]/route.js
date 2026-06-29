import { forwardToApi } from "@/lib/apiForward";

export async function GET(request, { params }) {
  const { id } = await params;
  return forwardToApi(`/api/v1/spvs/${id}`);
}

export async function PATCH(request, { params }) {
  const { id } = await params;
  const body = await request.json();
  return forwardToApi(`/api/v1/spvs/${id}`, { method: "PATCH", body });
}
