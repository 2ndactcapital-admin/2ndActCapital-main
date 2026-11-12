import { forwardToApi } from "@/lib/apiForward";

// CPSIM06 — "Open account at custodian" (SIMULATED custodian): the form's envelope (availability, CRM pre-fill with tax
// ids as last four only, the custodian's account types). Forwarded server-side with the caller's token; no org_id.
export async function GET(request, { params }) {
  const { id } = await params;
  return forwardToApi(`/api/v1/admin/households/${encodeURIComponent(id)}/custodian-account`);
}
