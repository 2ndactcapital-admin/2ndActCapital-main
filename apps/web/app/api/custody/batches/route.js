import { forwardToApi } from "@/lib/apiForward";

// Past import batches for the caller's org, newest first.
export async function GET() {
  return forwardToApi("/api/v1/custody/batches");
}
