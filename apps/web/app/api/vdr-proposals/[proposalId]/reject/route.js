import { forwardToApi } from "@/lib/apiForward";

// Chancery Phase 10 — reject a VDR proposal: no deal, no links. Rule 5.
export async function POST(request, { params }) {
  const { proposalId } = await params;
  return forwardToApi(`/api/v1/vdr-proposals/${proposalId}/reject`, {
    method: "POST",
  });
}
