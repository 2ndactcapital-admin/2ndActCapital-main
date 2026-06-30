import { forwardToApi } from "@/lib/apiForward";

export async function POST(request, { params }) {
  const { id, txnId } = await params;
  return forwardToApi(`/api/v1/spvs/${id}/transactions/${txnId}/post`, {
    method: "POST",
    body: {},
  });
}
