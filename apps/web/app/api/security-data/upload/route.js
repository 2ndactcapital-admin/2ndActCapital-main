import { forwardMultipartToApi } from "@/lib/apiForwardMultipart";

// MARKETDATA1 — a security-data file upload (multipart: file, data_type, licence JSON, mapping JSON).
// The upload is staged for a second admin's approval; nothing counts before that.
export async function POST(request) {
  return forwardMultipartToApi("/api/v1/security-data/uploads", request);
}
