// VISIBILITY1 — the pure render rules for the staff-visibility screens (Team panel, Unassigned families, Preview
// & switch). Every write control is decided from the server's own envelope and FAILS CLOSED: a missing envelope,
// a missing permissions block or a non-true can_write renders no control. Labels (reasons, role labels) come only
// from the envelope's vocabularies — never a client-side list.

export function canWrite(envelope) {
  return Boolean(envelope && envelope.permissions && envelope.permissions.can_write === true);
}

export function editable(envelope, field) {
  if (!canWrite(envelope)) return false;
  const list = envelope?.vocabularies?.editable;
  return Array.isArray(list) && list.includes(field);
}

/** The Team panel's controls: add / remove only with can_write AND "assignments" editable. */
export function teamControls(envelope) {
  const ok = editable(envelope, "assignments");
  return { add: ok, remove: ok };
}

export function roleLabels(envelope) {
  const l = envelope?.vocabularies?.role_labels;
  return Array.isArray(l) ? l.filter((x) => typeof x === "string") : [];
}

export function reasonText(reason, vocabularies) {
  if (!reason) return "";
  const labels = vocabularies?.reason_labels || {};
  const base = reason.label || labels[reason.kind] || "";
  const bits = [];
  if (reason.team) bits.push(reason.team);
  if (reason.via) bits.push(`via ${reason.via}`);
  if (reason.role_label) bits.push(reason.role_label);
  return bits.length ? `${base} (${bits.join(", ")})` : base;
}

export function assigneeName(a) {
  if (!a) return "";
  if (a.assigned_to_team_id) return `Team: ${a.assigned_to_team_name || "—"}`;
  return a.assigned_to_user_name || "—";
}

export function ownerLine(owner) {
  const names = (list) => (Array.isArray(list) ? list.map((u) => u.name || "—") : []);
  const rm = names(owner?.relationship_managers);
  const cb = names(owner?.created_by);
  const parts = [];
  if (rm.length) parts.push(`Relationship manager: ${rm.join(", ")}`);
  if (cb.length) parts.push(`Created by: ${cb.join(", ")}`);
  return parts.length ? parts.join(" · ") : "No owner recorded";
}

/** Preview: the switch control renders only for an org admin envelope (can_write + "enforced" editable). */
export function switchControl(preview) {
  return editable(preview, "enforced");
}

export function previewRows(preview) {
  const people = Array.isArray(preview?.people) ? preview.people : [];
  return people.map((p) => ({
    userId: p.user_id,
    name: p.name || p.email || "—",
    count: p.sees_all ? "All" : String(p.family_count ?? 0),
    seesAll: Boolean(p.sees_all),
    families: Array.isArray(p.families) ? p.families : [],
  }));
}

export function seesNone(preview) {
  return Array.isArray(preview?.sees_none) ? preview.sees_none : [];
}

/** The sentence on the confirm step — what switching will do, from the server's numbers only. */
export function confirmSummary(preview, turningOn) {
  if (!preview) return "";
  if (!turningOn) return "Every internal user will again see every family in the organisation.";
  const none = seesNone(preview).length;
  const lines = [`Staff will see only the families assigned to them, their teams and the people who report to them.`];
  lines.push(`${preview.unassigned_count ?? 0} of ${preview.families_total ?? 0} families have no one assigned — only org admins and their owner will see them.`);
  if (none) lines.push(`${none} staff member(s) will see no family at all.`);
  return lines.join(" ");
}

export function unassignedRows(envelope) {
  return Array.isArray(envelope?.rows) ? envelope.rows : [];
}
