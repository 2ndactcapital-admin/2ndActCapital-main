import { forwardToApi } from "@/lib/apiForward";

// VISIBILITY1 — the Team panel of a household record: read, and add an assignment (audited server-side).
export async function GET(request, { params }) {
  const { id } = await params;
  return forwardToApi(`/api/v1/staff-visibility/households/${encodeURIComponent(id)}/team`);
}

export async function POST(request, { params }) {
  const { id } = await params;
  const body = await request.json();
  return forwardToApi(`/api/v1/staff-visibility/households/${encodeURIComponent(id)}/team`, { method: "POST", body });
}
