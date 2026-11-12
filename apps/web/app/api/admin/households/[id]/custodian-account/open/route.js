import { forwardToApi } from "@/lib/apiForward";

// CPSIM06 — "Open account at custodian": open. A typed tax id / date of birth is forwarded once to the API, which sends it
// to the SIMULATED custodian and never stores or returns it. No org_id is added here (Rule 6).
export async function POST(request, { params }) {
  const { id } = await params;
  const body = await request.json();
  return forwardToApi(`/api/v1/admin/households/${encodeURIComponent(id)}/custodian-account/open`, { method: "POST", body });
}
