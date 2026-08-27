import { forwardToApi } from "@/lib/apiForward";

// Custodian profiles the caller's org can import from, plus the caller's write
// flag. CLAUDE.md Rule 5: the browser never calls FastAPI directly, and there
// is deliberately nothing here that would let a caller supply an org — it comes
// from the access token server-side.
export async function GET() {
  return forwardToApi("/api/v1/custody/profiles");
}
