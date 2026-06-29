import { forwardToApi } from "@/lib/apiForward";

export async function GET(request) {
  const { searchParams } = new URL(request.url);
  const params = {};
  for (const [k, v] of searchParams.entries()) params[k] = v;
  return forwardToApi("/api/v1/spvs", { searchParams: params });
}

export async function POST(request) {
  const body = await request.json();
  return forwardToApi("/api/v1/spvs", { method: "POST", body });
}
