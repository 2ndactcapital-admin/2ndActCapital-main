import { forwardToApi } from "@/lib/apiForward";

export async function GET(request) {
  const { searchParams } = new URL(request.url);
  const qs = searchParams.toString();
  const path = `/api/v1/allocation-lens${qs ? `?${qs}` : ""}`;
  return forwardToApi(path);
}
