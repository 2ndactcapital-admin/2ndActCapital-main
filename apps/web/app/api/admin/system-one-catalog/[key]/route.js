import { forwardToApi } from "@/lib/apiForward";

export async function DELETE(request, { params }) {
  const { key } = await params;
  return forwardToApi(
    `/api/v1/admin/system-one-catalog/${encodeURIComponent(key)}`,
    { method: "DELETE" },
  );
}
