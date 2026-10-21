// BUGFIX1 — what the deal page's pipeline shows, derived ONLY from the API's
// lifecycle envelope (GET /deals/{id}/lifecycle). No labels or stages here.
export function pipelineView(lifecycle, deal) {
  const stages = lifecycle?.stages || [];
  const status = lifecycle?.status ?? deal?.deal_status ?? null;
  const idx = stages.findIndex((s) => s.status === status);
  const labelOf = (st) => stages.find((s) => s.status === st)?.label || st;
  const nextStatus = (lifecycle?.next || []).find((n) => n !== "submitted") || null;
  const nextStage = nextStatus ? stages.find((s) => s.status === nextStatus) : null;
  return {
    stages: stages
      .filter((s) => s.status !== "archived" || status === "archived")
      .map((s, i) => ({
        status: s.status,
        label: s.label || s.status,
        isCurrent: s.status === status,
        isPast: idx >= 0 && stages.indexOf(s) < idx,
      })),
    canSubmit: status === "draft",
    missing: lifecycle?.missing_for_submit || [],
    next: nextStage ? { status: nextStage.status, key: nextStage.key, label: labelOf(nextStage.status) } : null,
    canArchive: Boolean(status) && status !== "archived",
    unarchive: status === "archived" && lifecycle?.unarchive_to
      ? { status: lifecycle.unarchive_to, label: lifecycle.unarchive_to_label || lifecycle.unarchive_to }
      : null,
  };
}
