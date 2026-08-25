import { forwardToApi } from "@/lib/apiForward";

// One transaction: the right-pane detail read.
//
// There is no PATCH here, and its absence is the point. portfolio.transactions
// is an append-only ledger, so an edit is a CORRECTION — see
// ./[transactionId]/corrections/route.js. A PATCH on this path would name a
// semantics the table does not have.
export async function GET(request, { params }) {
  const { transactionId } = await params;
  return forwardToApi(
    `/api/v1/portfolio/transactions/${encodeURIComponent(transactionId)}`,
  );
}
