import { forwardToApi } from "@/lib/apiForward";

export async function GET(request) {
  const { searchParams } = new URL(request.url);
  return forwardToApi("/api/v1/notifications", {
    searchParams: {
      status: searchParams.get("status"),
      limit: searchParams.get("limit"),
      offset: searchParams.get("offset"),
    },
  });
}

export async function PUT() {
  // Mark all unread notifications as read.
  return forwardToApi("/api/v1/notifications/read-all", { method: "PUT" });
}
