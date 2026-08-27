import { redirect } from "next/navigation";

// The per-run deep link.
//
// It used to be a SECOND renderer of a run — its own page, its own header, its
// own steps table — parallel to the list. Run History now has a detail pane, so
// keeping both would mean two places where a run's origin, its timeline and its
// held-run alerts each have to be got right, and only one of them would be.
//
// This route stays because links to it exist (and because a bookmarked run id
// should still resolve): it hands off to the one screen, with that run selected.
export default async function WorkflowRunDetailPage({ params }) {
  const { runId } = await params;
  redirect(`/admin/workflows/runs?run=${encodeURIComponent(runId)}`);
}
