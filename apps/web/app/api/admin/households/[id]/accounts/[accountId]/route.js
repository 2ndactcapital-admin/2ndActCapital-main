import { forwardToApi } from "@/lib/apiForward";

// PORTENTRY2 (MUSE-OLIVO-H-01) — correct an account's registration type, tax status, billable / discretionary flags
// or service model.
export async function PATCH(request, { params }) {
  const { id, accountId } = await params;
  const body = await request.json();
  return forwardToApi(
    `/api/v1/admin/households/${encodeURIComponent(id)}/accounts/${encodeURIComponent(accountId)}`,
    { method: "PATCH", body },
  );
}
