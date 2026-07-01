import { forwardToApi } from "@/lib/apiForward";

export async function GET(request) {
  return forwardToApi(`/api/v1/entity-groups`);
}

export async function POST(request) {
  const body = await request.json();
  return forwardToApi(`/api/v1/entity-groups`, { method: "POST", body });
}
