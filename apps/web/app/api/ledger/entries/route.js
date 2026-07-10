import { forwardToApi } from "@/lib/apiForward";

export async function GET(request) {
  const { searchParams } = new URL(request.url);
  const params = {};
  if (searchParams.get("vehicle_id")) params.vehicle_id = searchParams.get("vehicle_id");
  if (searchParams.get("from")) params.from = searchParams.get("from");
  if (searchParams.get("to")) params.to = searchParams.get("to");
  return forwardToApi("/api/v1/ledger/entries", { searchParams: params });
}

export async function POST(request) {
  const body = await request.json();
  return forwardToApi("/api/v1/ledger/entries", { method: "POST", body });
}
