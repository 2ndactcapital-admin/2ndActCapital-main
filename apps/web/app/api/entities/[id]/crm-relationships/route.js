import { forwardToApi } from "@/lib/apiForward";

// PORTENTRY2 (MUSE-OLIVO-E-02 / D-02) — never served from a cache: a saved row must show on the next read.
export const dynamic = "force-dynamic";

// CRMDETAIL1 — the record's governed relationships (GET) and "add a relationship" (POST). Server-side forward; the
// browser never calls FastAPI and never sends an org_id.
export async function GET(request, { params }) {
  const { id } = await params;
  return forwardToApi(`/api/v1/entities/${encodeURIComponent(id)}/crm-relationships`);
}

export async function POST(request, { params }) {
  const { id } = await params;
  const body = await request.json();
  return forwardToApi(`/api/v1/entities/${encodeURIComponent(id)}/crm-relationships`, { method: "POST", body });
}
