import { forwardToApi } from "@/lib/apiForward";

// One batch and its exception list. The batch id is scoped to the caller's org
// server-side, so a guessed id from another tenant reads as a 404, not a leak.
export async function GET(request, { params }) {
  const { batchId } = await params;
  return forwardToApi(`/api/v1/custody/batches/${encodeURIComponent(batchId)}`);
}
