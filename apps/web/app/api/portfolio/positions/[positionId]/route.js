import { forwardToApi } from "@/lib/apiForward";

// One position: the right-pane detail read, and the inline/pane edit.
//
// PATCH returns the SUCCESSOR position, with a different id — the edit is a
// bi-temporal restatement (CLAUDE.md Rule 3), not an in-place update. The
// client must adopt the returned id; continuing to use the one it sent would
// mean reading a row that is now history.
export async function GET(request, { params }) {
  const { positionId } = await params;
  const valueAsOf = new URL(request.url).searchParams.get("value_as_of");
  return forwardToApi(
    `/api/v1/portfolio/positions/${encodeURIComponent(positionId)}`,
    { searchParams: valueAsOf ? { value_as_of: valueAsOf } : undefined },
  );
}

export async function PATCH(request, { params }) {
  const { positionId } = await params;
  const body = await request.json();
  return forwardToApi(
    `/api/v1/portfolio/positions/${encodeURIComponent(positionId)}`,
    { method: "PATCH", body },
  );
}
