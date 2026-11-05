import { forwardToApi } from "@/lib/apiForward";

// PORTENTRY2 (MUSE-OLIVO-E-02 / D-02) — never served from a cache: a saved row must show on the next read.
export const dynamic = "force-dynamic";

// CRMDETAIL1 — typed detail fields (labels and types from the API).
export async function GET(request, { params }) {
  const { id } = await params;
  return forwardToApi(`/api/v1/entities/${encodeURIComponent(id)}/details`);
}

export async function PUT(request, { params }) {
  const { id } = await params;
  const body = await request.json();
  return forwardToApi(`/api/v1/entities/${encodeURIComponent(id)}/details`, { method: "PUT", body });
}
