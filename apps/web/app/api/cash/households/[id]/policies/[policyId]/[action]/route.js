import { forwardToApi } from "@/lib/apiForward";

// CASH02 — submit a draft for approval, or approve / reject it (a different person; the API re-checks everything).
const ACTIONS = new Set(["submit", "approve", "reject"]);

export async function POST(request, { params }) {
  const { id, policyId, action } = await params;
  if (!ACTIONS.has(action)) return Response.json({ detail: "Not found" }, { status: 404 });
  const body = await request.json().catch(() => ({}));
  return forwardToApi(
    `/api/v1/cash/households/${encodeURIComponent(id)}/policies/${encodeURIComponent(policyId)}/${action}`,
    { method: "POST", body },
  );
}
