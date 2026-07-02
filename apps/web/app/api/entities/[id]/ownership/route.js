import { forwardToApi } from "@/lib/apiForward";

export async function GET(request, { params }) {
  const { id } = await params;
  const { searchParams } = new URL(request.url);
  const as_of = searchParams.get("as_of");
  const sp = as_of ? { as_of } : undefined;
  return forwardToApi(`/api/v1/entities/${id}/ownership`, { searchParams: sp });
}

export async function POST(request, { params }) {
  const { id } = await params;
  const body = await request.json();
  return forwardToApi(`/api/v1/entities/${id}/ownership`, { method: "POST", body });
}
