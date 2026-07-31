import { forwardToApi } from "@/lib/apiForward";

// Chancery Phase 10 — pending VDR deal-proposals for the caller's org. Rule 5:
// the browser calls this Next route, never FastAPI directly.
export async function GET() {
  return forwardToApi("/api/v1/vdr-proposals");
}
