import { forwardToApi } from "@/lib/apiForward";

export async function GET(request) {
  const { searchParams } = new URL(request.url);
  return forwardToApi("/api/v1/assistant/conversation", {
    searchParams: {
      context_type: searchParams.get("context_type"),
      context_id: searchParams.get("context_id"),
    },
  });
}
