import { forwardToApi } from "@/lib/apiForward";

export async function POST(request) {
  const body = await request.json().catch(() => ({}));
  return forwardToApi("/api/v1/assistant/message", { method: "POST", body });
}
