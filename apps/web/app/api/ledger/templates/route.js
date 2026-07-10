import { forwardToApi } from "@/lib/apiForward";

export async function GET(request) {
  const { searchParams } = new URL(request.url);
  const vehicle_id = searchParams.get("vehicle_id");
  return forwardToApi("/api/v1/ledger/templates", {
    searchParams: vehicle_id ? { vehicle_id } : undefined,
  });
}
