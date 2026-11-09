import { forwardToApi } from "@/lib/apiForward";

// CASH05 — Treasury holdings per sleeve, every ladder rung (read-only; the API decides the scope — another family's household is a 404).
const KEYS = ["entity_id"];

export async function GET(request, { params }) {
  const { id } = await params;
  const q = new URL(request.url).searchParams;
  const searchParams = Object.fromEntries(KEYS.map((k) => [k, q.get(k)]).filter(([, v]) => v !== null && v !== ""));
  return forwardToApi(`/api/v1/cash/households/${encodeURIComponent(id)}/holdings`, { searchParams });
}
