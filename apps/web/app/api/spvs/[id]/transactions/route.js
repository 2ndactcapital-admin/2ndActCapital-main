import { forwardToApi } from "@/lib/apiForward";

export async function GET(request, { params }) {
  const { id } = await params;
  return forwardToApi(`/api/v1/spvs/${id}/transactions`);
}

export async function POST(request, { params }) {
  const { id } = await params;
  const body = await request.json();
  return forwardToApi(`/api/v1/spvs/${id}/transactions`, { method: "POST", body });
}
