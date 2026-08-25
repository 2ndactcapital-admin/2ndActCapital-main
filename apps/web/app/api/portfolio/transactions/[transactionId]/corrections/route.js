import { forwardToApi } from "@/lib/apiForward";

// Correct a transaction.
//
// POST, and 201, because this MINTS a row: the original is closed
// (valid_to = now()) and a successor is recorded pointing back at it. The
// response carries a DIFFERENT transaction id — the client must adopt it, or
// every subsequent read would be against a row that is now history.
export async function POST(request, { params }) {
  const { transactionId } = await params;
  const body = await request.json();
  return forwardToApi(
    `/api/v1/portfolio/transactions/${encodeURIComponent(transactionId)}/corrections`,
    { method: "POST", body },
  );
}
