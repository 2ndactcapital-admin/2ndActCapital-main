import { forwardToApi } from "@/lib/apiForward";

// One asset — detail and correction.
//
// PATCH returns the SAME id. Unlike a position, whose edit restates on the
// valid axis and mints a new id, an asset is a referenced master row
// (asset_identifiers, positions and valuations all carry an FK to assets.id):
// the outgoing version is archived on the SYSTEM axis and the live row keeps
// its id. A client that swapped ids here would be inventing a change the API
// did not make.
//
// The body is forwarded VERBATIM. It is not filtered here, and that is
// deliberate rather than lazy: FastAPI owns the only copy of the org-editable
// and platform-sourced field lists, and answers 403 for a platform field and
// 422 for genuine junk. A second, partial copy of that list in this file would
// drift, and the drift would show up as a legal edit silently dropped on its
// way to an endpoint that would have accepted it.
//
// Rule 5 as everywhere else: org_id travels in the token, never in the body.
export async function GET(request, { params }) {
  const { assetId } = await params;
  const valueAsOf = new URL(request.url).searchParams.get("value_as_of");
  return forwardToApi(
    `/api/v1/portfolio/securities/${encodeURIComponent(assetId)}`,
    { searchParams: valueAsOf ? { value_as_of: valueAsOf } : undefined },
  );
}

export async function PATCH(request, { params }) {
  const { assetId } = await params;
  const body = await request.json();
  return forwardToApi(
    `/api/v1/portfolio/securities/${encodeURIComponent(assetId)}`,
    { method: "PATCH", body },
  );
}
