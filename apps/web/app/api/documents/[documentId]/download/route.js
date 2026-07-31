import { forwardToApi } from "@/lib/apiForward";

// "See attached document" — proxy to the Chancery download endpoint, which
// returns a short-lived presigned R2 URL for the stored original. The browser
// opens that URL in a new tab (the review screen never sees storage_key).
export async function GET(request, { params }) {
  const { documentId } = await params;
  return forwardToApi(`/api/v1/documents/${documentId}/download`);
}
