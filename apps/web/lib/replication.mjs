// REP1 — Replication Studio: the pure logic behind the registry and entry
// screens. Every write control is decided HERE from the server's envelope and
// fails CLOSED: only `=== true` on the server's own flag shows a control (a
// missing, partial or malformed envelope shows none). Every label comes from
// the envelope's vocabularies; nothing here holds a list of fields, origins,
// statuses or classes of its own.

const isTrue = (v) => v === true;

export function perms(envelope) {
  const p = envelope && typeof envelope === "object" ? envelope.permissions : null;
  return p && typeof p === "object" ? p : {};
}

/** Registry list: the Add button. */
export function registryControls(envelope) {
  return { add: isTrue(perms(envelope).can_write) };
}

/** One terms field: Correct (wrong read) and Change (deliberate departure). */
export function fieldControls(envelope, fieldKey) {
  const p = perms(envelope);
  const v = envelope?.vocabularies || {};
  const canWrite = isTrue(p.can_write);
  const inList = (list) => Array.isArray(list) && list.includes(fieldKey);
  return {
    correct: canWrite && inList(v.correctable) && Boolean(envelope?.terms?.draft_version_id),
    change: canWrite && inList(v.changeable),
  };
}

/** Approve / Reject: only with approve_replication AND not a maker of the draft. */
export function approvalControls(envelope) {
  const p = perms(envelope);
  const show = isTrue(p.can_approve) && isTrue(p.can_approve_draft) && !isTrue(p.is_maker_of_draft)
    && Boolean(envelope?.terms?.draft_version_id);
  return { approve: show, reject: show };
}

/** Entry page actions: Refresh holdings / Re-run extraction. */
export function entryControls(envelope) {
  const canWrite = isTrue(perms(envelope).can_write);
  return { refresh: canWrite, extract: canWrite };
}

export function labelOf(list, key) {
  if (!Array.isArray(list)) return key ?? "";
  const hit = list.find((x) => x && x.key === key);
  return hit ? hit.label : (key ?? "");
}

export function formatValue(value) {
  if (value === null || value === undefined || value === "") return "—";
  if (value === true) return "Yes";
  if (value === false) return "No";
  return String(value);
}

/** "Departs from the fund: …" — one plain sentence per departure. */
export function departureLines(departures) {
  if (!Array.isArray(departures)) return [];
  return departures.map((d) => `Departs from the fund: ${d.label || d.field_key} is ${formatValue(d.our_value)}`
    + ` instead of the fund's ${formatValue(d.fund_value)}${d.reason ? ` — ${d.reason}` : ""}.`);
}

export function displayName(ticker) {
  return `Holdings of ${String(ticker || "").toUpperCase()}`;
}

export function disclosure(ticker) {
  const t = String(ticker || "").toUpperCase();
  return `Replicates the published holdings of ${t}; not affiliated with or endorsed by the fund's sponsor or index provider.`;
}

/** Weight fraction (string from the API) → "8.04%". */
export function formatWeight(w) {
  if (w === null || w === undefined || w === "") return "—";
  const n = Number(w);
  if (!Number.isFinite(n)) return "—";
  return `${(n * 100).toFixed(2)}%`;
}

export function termsStatus(row, versionStatuses) {
  const t = row?.terms || {};
  if (t.draft_version != null) {
    const base = `${labelOf(versionStatuses, "draft")} v${t.draft_version}`;
    return t.draft_needs_review > 0 ? `${base} — ${t.draft_needs_review} need review` : base;
  }
  if (t.approved_version != null) return `${labelOf(versionStatuses, "approved")} v${t.approved_version}`;
  return "—";
}

export function constituentsCell(row) {
  if (!row || !row.constituents_total) return "—";
  return `${row.constituents_resolved} / ${row.constituents_total}`;
}

/** Diff summary lines for the Holdings tab. */
export function diffSummary(diff) {
  if (!diff || diff.first_snapshot) return { first: true, added: [], removed: [], moves: [], turnover: null };
  return { first: false, added: diff.added || [], removed: diff.removed || [], moves: diff.moves || [],
    turnover: diff.turnover ?? null };
}
