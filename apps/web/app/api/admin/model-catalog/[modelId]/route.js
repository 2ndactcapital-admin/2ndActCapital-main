import { forwardToApi } from "@/lib/apiForward";

export async function DELETE(request, { params }) {
  const { modelId } = await params;
  return forwardToApi(
    `/api/v1/admin/model-catalog/${encodeURIComponent(modelId)}`,
    { method: "DELETE" },
  );
}
