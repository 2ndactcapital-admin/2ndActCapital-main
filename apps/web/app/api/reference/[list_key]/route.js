import { forwardToApi } from "@/lib/apiForward";

export async function GET(request, { params }) {
  const { list_key } = await params;
  const { searchParams } = new URL(request.url);
  const qp = {};
  for (const [k, v] of searchParams.entries()) qp[k] = v;
  return forwardToApi(`/api/v1/reference/${list_key}`, { searchParams: qp });
}
