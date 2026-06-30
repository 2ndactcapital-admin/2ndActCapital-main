import { forwardToApi } from "@/lib/apiForward";

export async function GET() {
  return forwardToApi("/api/v1/users/me");
}

export async function PATCH(request) {
  const body = await request.json();
  return forwardToApi("/api/v1/users/me", { method: "PATCH", body });
}
