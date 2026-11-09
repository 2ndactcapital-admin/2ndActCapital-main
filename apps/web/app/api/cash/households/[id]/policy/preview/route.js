import { forwardToApi } from "@/lib/apiForward";

// CASH02 — the derived sleeves / yields / coverage for an UNSAVED policy edit (writes nothing).
export async function POST(request, { params }) {
  const { id } = await params;
  const body = await request.json().catch(() => ({}));
  return forwardToApi(`/api/v1/cash/households/${encodeURIComponent(id)}/policy/preview`, { method: "POST", body });
}
