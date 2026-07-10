import { forwardToApi } from "@/lib/apiForward";

export async function POST(request, { params }) {
  const { id } = await params;
  return forwardToApi(`/api/v1/ledger/entries/${id}/post`, { method: "POST" });
}
