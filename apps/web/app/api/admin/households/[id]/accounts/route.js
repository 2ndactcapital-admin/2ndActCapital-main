import { forwardToApi } from "@/lib/apiForward";

// PERMS1 (REP-01) — "Add account": the typed number is forwarded once to the
// API, which stores only its mask and keyed hash and never returns it.
export async function POST(request, { params }) {
  const { id } = await params;
  const body = await request.json();
  return forwardToApi(`/api/v1/admin/households/${encodeURIComponent(id)}/accounts`, { method: "POST", body });
}
