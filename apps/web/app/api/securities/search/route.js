import { forwardToApi } from "@/lib/apiForward";

// PORTENTRY2 (MUSE-OLIVO-P.1) — read-only security-master search for internal staff (the API refuses a member).
export async function GET(request) {
  const { searchParams } = new URL(request.url);
  return forwardToApi("/api/v1/securities/search", {
    searchParams: { q: searchParams.get("q") || "", limit: searchParams.get("limit") || undefined },
  });
}
