import { forwardToApi } from "@/lib/apiForward";

export async function GET(request) {
  const { searchParams } = new URL(request.url);
  return forwardToApi("/api/v1/assistant/activities", {
    searchParams: {
      status: searchParams.get("status"),
      limit: searchParams.get("limit"),
    },
  });
}
