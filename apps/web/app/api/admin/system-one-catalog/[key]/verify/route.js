import { forwardToApi } from "@/lib/apiForward";

// Runs the real availability check: a models listing and one minimal
// decision through the LiteLLM proxy's /typesafe pass-through.
export async function POST(request, { params }) {
  const { key } = await params;
  return forwardToApi(
    `/api/v1/admin/system-one-catalog/${encodeURIComponent(key)}/verify`,
    { method: "POST" },
  );
}
