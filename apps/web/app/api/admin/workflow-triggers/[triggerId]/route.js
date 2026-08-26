import { forwardToApi } from "@/lib/apiForward";

// One trigger: edit / pause / resume (PATCH) and delete (DELETE).
//
// PAUSE AND DELETE ARE DIFFERENT REQUESTS, not two shades of one. Pause is
// PATCH {"is_active": false} — the API merges that over the stored row, so the
// recurrence, the bounds, the cap, occurrence_count and last_fired_at all
// survive and resuming restores the trigger exactly. DELETE removes the row and
// cannot be undone. The screen keeps them visually and interactively separate
// for the same reason.
//
// PATCH is sparse: only the keys actually present in the body change. Sending
// `{"is_active": false}` must not be turned into a full-row write here, or a
// pause would blank every field the client did not happen to have loaded.
export async function PATCH(request, { params }) {
  const { triggerId } = await params;
  const body = await request.json();
  return forwardToApi(
    `/api/v1/admin/workflow-triggers/${encodeURIComponent(triggerId)}`,
    { method: "PATCH", body },
  );
}

export async function DELETE(request, { params }) {
  const { triggerId } = await params;
  return forwardToApi(
    `/api/v1/admin/workflow-triggers/${encodeURIComponent(triggerId)}`,
    { method: "DELETE" },
  );
}
