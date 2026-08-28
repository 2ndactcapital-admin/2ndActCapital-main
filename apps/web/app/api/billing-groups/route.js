import { forwardToApi } from "@/lib/apiForward";

// Billing groups for the caller's org. Sprint fee33.
//
// org_id is never sent from here — the backend reads it from the caller's own
// verified token claims (CLAUDE.md Rule 6), so there is nothing on this side of
// the forward that could name a different tenant.
export async function GET(request) {
  const { searchParams } = new URL(request.url);
  return forwardToApi("/api/v1/billing-groups", {
    searchParams: {
      group_type: searchParams.get("group_type"),
      household_id: searchParams.get("household_id"),
    },
  });
}

export async function POST(request) {
  const body = await request.json();
  return forwardToApi("/api/v1/billing-groups", { method: "POST", body });
}
