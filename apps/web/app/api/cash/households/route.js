import { forwardToApi } from "@/lib/apiForward";

// CASH01 — the households the caller may open on Treasury. The API decides who may (view_portfolio / manage_portfolio; a
// member sees only their own family's).
export async function GET() {
  return forwardToApi("/api/v1/cash/households");
}
