import { forwardToApi } from "@/lib/apiForward";

// ensemblesystemone — the System One model catalog. GET is readable by any
// authenticated user (the envelope's can_write decides the controls); every
// write is super_admin only. FastAPI is the real gate; this just forwards.
export async function GET() {
  return forwardToApi("/api/v1/admin/system-one-catalog");
}

export async function POST(request) {
  const body = await request.json();
  return forwardToApi("/api/v1/admin/system-one-catalog", { method: "POST", body });
}
