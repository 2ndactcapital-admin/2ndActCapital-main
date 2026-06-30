import { forwardToApi } from "@/lib/apiForward";

export async function POST() {
  return forwardToApi("/api/v1/dashboard/todos/regenerate", { method: "POST", body: {} });
}
