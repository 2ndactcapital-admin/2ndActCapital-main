import { forwardMultipartToApi } from "@/lib/apiForwardMultipart";

// Step 4: commit. The backend rebuilds the plan from the re-posted file rather
// than trusting one the browser sends back — a client-supplied plan would be a
// set of database writes chosen by the caller, including which entity each
// account attaches to.
export async function POST(request) {
  return forwardMultipartToApi("/api/v1/custody/import/commit", request);
}
