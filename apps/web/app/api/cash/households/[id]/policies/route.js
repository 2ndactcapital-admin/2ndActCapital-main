import { forwardToApi } from "@/lib/apiForward";

// CASH02 — save a cash policy DRAFT (a new version, or an edit of the open draft). Never activates it.
export async function POST(request, { params }) {
  const { id } = await params;
  const body = await request.json().catch(() => ({}));
  return forwardToApi(`/api/v1/cash/households/${encodeURIComponent(id)}/policies`, { method: "POST", body });
}
