import { forwardToApi } from "@/lib/apiForward";

// PORTENTRY2 (MUSE-OLIVO-PERS-01) — the record's write envelope (services/record_permissions on the API).
export async function GET(_request, { params }) {
  const { id } = await params;
  return forwardToApi(`/api/v1/entities/${encodeURIComponent(id)}/permissions`);
}
