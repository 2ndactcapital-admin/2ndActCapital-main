import { forwardMultipartToApi } from "@/lib/apiForwardMultipart";

// Step 3: the diff. Writes nothing to the account tables — the response is what
// a commit WOULD do, split into new accounts, changed balances, new flows and
// unmatched rows.
export async function POST(request) {
  return forwardMultipartToApi("/api/v1/custody/import/dry-run", request);
}
