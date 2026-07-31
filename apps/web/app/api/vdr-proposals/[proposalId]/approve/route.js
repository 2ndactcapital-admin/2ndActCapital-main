import { forwardToApi } from "@/lib/apiForward";

// Chancery Phase 10 — approve a VDR proposal: creates a REAL deal via the shared
// createDeal core and links every document in the drop. Optional JSON body
// {fields:{...}} carries reviewer edits (e.g. taxonomy keys). Rule 5.
export async function POST(request, { params }) {
  const { proposalId } = await params;
  let body;
  try {
    body = await request.json();
  } catch {
    body = undefined;
  }
  return forwardToApi(`/api/v1/vdr-proposals/${proposalId}/approve`, {
    method: "POST",
    body: body || {},
  });
}
