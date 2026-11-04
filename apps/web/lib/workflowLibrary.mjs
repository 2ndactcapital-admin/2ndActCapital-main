/**
 * BUGFIX2 — the workflow library's delete control. It renders only when the
 * server published `can_delete === true` for the row (the caller holds
 * author_workflows AND the workflow has never run). Anything else — false,
 * missing, a truthy non-boolean — renders no control: fails closed.
 */
export function canDeleteWorkflow(w) {
  return w?.can_delete === true;
}

/** The server's reason a row cannot be deleted, or null. */
export function deleteRefusal(w) {
  return typeof w?.delete_refusal === "string" && w.delete_refusal ? w.delete_refusal : null;
}

/** PERMS1 (WF-06) — the confirmation names the workflow it deletes. */
export function deleteConfirmText(w) {
  const name = typeof w?.name === "string" && w.name ? w.name : "this workflow";
  return `Delete “${name}” and all its versions? This cannot be undone.`;
}
