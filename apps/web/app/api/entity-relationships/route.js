import { forwardToApi } from "@/lib/apiForward";

export async function POST(request) {
  const body = await request.json();
  return forwardToApi(`/api/v1/entity-relationships`, { method: "POST", body });
}
