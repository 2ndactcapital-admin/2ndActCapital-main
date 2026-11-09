import { forwardToApi } from "@/lib/apiForward";

// CASH04 — a person adopts the maturity waterfall's draft (manage_proposals): a new proposal, submitted for a
// DIFFERENT person's approval.
export async function POST(_request, { params }) {
  const { proposalId } = await params;
  return forwardToApi(`/api/v1/cash/proposals/${encodeURIComponent(proposalId)}/adopt`, { method: "POST", body: {} });
}
