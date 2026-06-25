import { forwardToApi } from "@/lib/apiForward";

export async function GET() {
  return forwardToApi("/api/v1/notifications/count");
}
