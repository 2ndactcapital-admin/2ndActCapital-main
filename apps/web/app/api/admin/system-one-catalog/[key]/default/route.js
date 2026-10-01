import { forwardToApi } from "@/lib/apiForward";

export async function PUT(request, { params }) {
  const { key } = await params;
  return forwardToApi(
    `/api/v1/admin/system-one-catalog/${encodeURIComponent(key)}/default`,
    { method: "PUT" },
  );
}
