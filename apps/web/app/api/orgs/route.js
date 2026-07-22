import { forwardToApi } from "@/lib/apiForward";

// Super Admin only — the backend enforces it; this route just forwards.
export async function GET() {
  return forwardToApi("/api/v1/orgs");
}

export async function POST(request) {
  const body = await request.json();
  return forwardToApi("/api/v1/orgs", { method: "POST", body });
}
