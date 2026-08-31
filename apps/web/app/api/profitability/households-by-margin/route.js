import { forwardToApi } from "@/lib/apiForward";

export async function GET(request) {
  const { searchParams } = new URL(request.url);
  const qs = searchParams.toString();
  return forwardToApi(
    `/api/v1/profitability/households-by-margin${qs ? `?${qs}` : ""}`,
  );
}
