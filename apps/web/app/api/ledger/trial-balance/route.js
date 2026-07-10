import { forwardToApi } from "@/lib/apiForward";

export async function GET(request) {
  const { searchParams } = new URL(request.url);
  const params = {};
  if (searchParams.get("vehicle_id")) params.vehicle_id = searchParams.get("vehicle_id");
  if (searchParams.get("basis")) params.basis = searchParams.get("basis");
  if (searchParams.get("as_of")) params.as_of = searchParams.get("as_of");
  return forwardToApi("/api/v1/ledger/trial-balance", { searchParams: params });
}
