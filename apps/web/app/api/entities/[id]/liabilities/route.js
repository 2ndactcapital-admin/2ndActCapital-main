import { forwardToApi } from "@/lib/apiForward";

// CRMDETAIL1 — the record's liabilities + net worth (GET) and "add a liability" (POST).
export async function GET(request, { params }) {
  const { id } = await params;
  return forwardToApi(`/api/v1/entities/${encodeURIComponent(id)}/liabilities`);
}

export async function POST(request, { params }) {
  const { id } = await params;
  const body = await request.json();
  return forwardToApi(`/api/v1/entities/${encodeURIComponent(id)}/liabilities`, { method: "POST", body });
}
