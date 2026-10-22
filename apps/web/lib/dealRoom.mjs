/**
 * DEALROOM1 — pure view models for the deal room and the access grid.
 *
 * Every decision here reads the SERVER's envelope; nothing has a default that
 * could restore access. `can_write` is true only when the server said exactly
 * `true`; a missing or malformed envelope yields no write control (fails
 * CLOSED). Labels come from the envelope's vocabularies (config, Rule 1).
 * Kept free of React so node tests run it directly.
 */

/** True only when the server's envelope says the caller may write. */
export function canWrite(envelope) {
  return envelope?.permissions?.can_write === true;
}

/**
 * Where the investor is in the path:
 *   manage        the caller manages the deal (publisher / delegate / staff)
 *   request       no access yet → choose investing entity
 *   not_eligible  eligibility failed (missing[] says why)
 *   sign          eligible; `next` is the first unsigned agreement in order
 *   awaiting      every agreement signed, publisher approves
 *   granted | declined | revoked
 */
export function investorStep(envelope) {
  if (!envelope) return { step: "unavailable" };
  if (envelope.rights?.can_manage === true) return { step: "manage" };
  const access = envelope.my_access;
  if (!access) return { step: "request" };
  const s = access.status;
  if (s === "requested") {
    const pending = (envelope.agreements || [])
      .filter((a) => !a.signed_at)
      .sort((a, b) => a.sort_order - b.sort_order);
    return pending.length ? { step: "sign", next: pending[0], remaining: pending.length } : { step: "awaiting" };
  }
  if (s === "signed_awaiting") return { step: "awaiting" };
  if (s === "not_eligible") return { step: "not_eligible", missing: access.missing || [] };
  if (["granted", "declined", "revoked"].includes(s)) return { step: s };
  return { step: "request" };
}

/** Folders as the room renders them: parents before their subfolders, each
 *  with a lock reason when closed and NO documents key when closed. */
export function folderRows(envelope) {
  const folders = envelope?.folders || [];
  const byParent = new Map();
  for (const f of folders) {
    const k = f.parent_id || "";
    if (!byParent.has(k)) byParent.set(k, []);
    byParent.get(k).push(f);
  }
  const out = [];
  const walk = (parent, depth) => {
    for (const f of (byParent.get(parent) || []).sort((a, b) => a.sort_order - b.sort_order)) {
      out.push({
        id: f.id, depth, label: f.label, level: f.effective_level,
        levelLabel: f.access_level_label, open: f.open === true,
        lockReason: f.open === true ? null : f.lock_reason || null,
        documents: f.open === true ? f.documents || [] : null,
        isStandard: f.is_standard === true, named: f.named || null,
      });
      walk(f.id, depth + 1);
    }
  };
  walk("", 0);
  return out;
}

/** The access grid's columns: fixed ones plus one per required agreement. */
export function gridColumns(envelope) {
  const agreementCols = (envelope?.agreement_columns || []).map((c) => ({
    field: `agreement:${c.key}`, headerName: c.title, agreementKey: c.key,
  }));
  return [
    { field: "investor_name", headerName: "Investor" },
    { field: "entity_name", headerName: "Investing entity" },
    { field: "deal_name", headerName: "Deal" },
    { field: "publisher_name", headerName: "Publisher" },
    { field: "eligibility", headerName: "Eligibility" },
    ...agreementCols,
    { field: "level_label", headerName: "Access level" },
    { field: "status_label", headerName: "Status" },
    { field: "decided", headerName: "Decided" },
  ];
}

/** The decision buttons the grid may show — none unless the server says so. */
export function gridActions(envelope) {
  if (!canWrite(envelope)) return [];
  return Array.isArray(envelope?.vocabularies?.actions) ? envelope.vocabularies.actions : [];
}

/** Cell text for one grid row and column. */
export function gridCell(row, col) {
  if (col.agreementKey) {
    const v = row.agreements?.[col.agreementKey];
    if (v === "n/a") return "n/a";
    return v ? v.slice(0, 10) : "—";
  }
  if (col.field === "eligibility") {
    const e = row.eligibility || {};
    return [e.accreditation_status, e.verified_date].filter(Boolean).join(" · ") || "—";
  }
  if (col.field === "decided") {
    return row.decided_at ? `${row.decided_by || "automatic"} · ${row.decided_at.slice(0, 10)}` : "—";
  }
  return row[col.field] ?? "—";
}

/** Rows grouped by the server-computed `group` label, in order. */
export function groupRows(rows) {
  const groups = [];
  for (const r of rows || []) {
    const last = groups[groups.length - 1];
    if (last && last.label === r.group) last.rows.push(r);
    else groups.push({ label: r.group, rows: [r] });
  }
  return groups;
}
