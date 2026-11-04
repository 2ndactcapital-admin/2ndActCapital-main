import { forwardToApi } from "@/lib/apiForward";

// PERMS1 (REP-01) — one household: members, accounts (masked numbers only).
export async function GET(_request, { params }) {
  const { id } = await params;
  return forwardToApi(`/api/v1/admin/households/${encodeURIComponent(id)}`);
}
