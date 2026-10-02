import { forwardToApi } from "@/lib/apiForward";

// modelresearch.structural — the Model Research grid (read-only).
//
// CLAUDE.md Rule 5: the browser never calls FastAPI, the LiteLLM proxy, or
// GitHub directly. Every source read happens server-side in FastAPI, which is
// also the real gate (super_admin or manage_org_settings; a member gets 403).
// Only `refresh` is forwarded. Nothing identifying an org passes through here.
export async function GET(request) {
  const refresh = new URL(request.url).searchParams.get("refresh");
  return forwardToApi("/api/v1/admin/model-research", {
    searchParams: { refresh: refresh === "true" ? "true" : undefined },
  });
}
