import { forwardToApi } from "@/lib/apiForward";

// The curated platform model list (LiteLLM Phase D2). GET is readable by any
// authenticated user (an org's own picker screen needs it); POST is
// super_admin only — the backend is the real gate, this just forwards.
export async function GET() {
  return forwardToApi("/api/v1/admin/model-catalog");
}

export async function POST(request) {
  const body = await request.json();
  return forwardToApi("/api/v1/admin/model-catalog", { method: "POST", body });
}
