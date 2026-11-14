import { forwardToApi } from "@/lib/apiForward";

// NAV3 — the record's primary household (the EXISTING GET /admin/entities/{id}/primary-household, manage_members).
export async function GET(request, { params }) {
  const { id } = await params;
  return forwardToApi(`/api/v1/admin/entities/${encodeURIComponent(id)}/primary-household`);
}
