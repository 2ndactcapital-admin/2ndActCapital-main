import { forwardMultipartToApi } from "@/lib/apiForwardMultipart";

// Step 1→2 of the import wizard: read the uploaded file's headers and a few
// sample rows so the operator can map columns. The backend masks anything
// account-number-shaped before it returns the sample.
export async function POST(request) {
  return forwardMultipartToApi("/api/v1/custody/import/inspect", request);
}
