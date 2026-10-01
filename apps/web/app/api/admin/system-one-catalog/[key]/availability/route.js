import { forwardToApi } from "@/lib/apiForward";

export async function PUT(request, { params }) {
  const { key } = await params;
  const body = await request.json();
  return forwardToApi(
    `/api/v1/admin/system-one-catalog/${encodeURIComponent(key)}/availability`,
    { method: "PUT", body },
  );
}
