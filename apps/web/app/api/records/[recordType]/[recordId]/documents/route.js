import { forwardToApi } from "@/lib/apiForward";

// Chancery Phase 9 — documents linked to a record, for the reusable Documents
// panel. Forwards to the collision-free FastAPI route (org_id travels in the JWT,
// never the path). record_type='entity' → entity-link query; otherwise the
// generic document_record_links query. Rule 5: the browser never calls FastAPI.
export async function GET(request, { params }) {
  const { recordType, recordId } = await params;
  return forwardToApi(
    `/api/v1/records/${encodeURIComponent(recordType)}/${encodeURIComponent(recordId)}/documents`,
  );
}
