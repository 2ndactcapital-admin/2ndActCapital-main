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
 * DEALROOM2: resign_required behaves like requested (sign what is no longer
 * satisfied — the server sends signed_at only for a signature that still
 * counts) and carries `resign: true`.
 */
export function investorStep(envelope) {
  if (!envelope) return { step: "unavailable" };
  if (envelope.rights?.can_manage === true) return { step: "manage" };
  const access = envelope.my_access;
  if (!access) return { step: "request" };
  const s = access.status;
  if (s === "requested" || s === "resign_required") {
    const pending = (envelope.agreements || [])
      .filter((a) => !a.signed_at)
      .sort((a, b) => a.sort_order - b.sort_order);
    const resign = s === "resign_required";
    if (!pending.length) return { step: "awaiting", resign };
    return resign ? { step: "sign", next: pending[0], remaining: pending.length, resign }
      : { step: "sign", next: pending[0], remaining: pending.length };
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
    const d = row.agreement_detail?.[col.agreementKey];
    if (d?.label) return d.label;
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

// ── DEALROOM2 ──────────────────────────────────────────────────────────────

/** True when a scrolling viewer has been read to its end (a 4px tolerance
 *  for sub-pixel rounding). Takes the element's three scroll measurements. */
export function isScrolledToEnd({ scrollTop = 0, clientHeight = 0, scrollHeight = 0 } = {}) {
  if (!(scrollHeight > 0)) return false;
  return scrollTop + clientHeight >= scrollHeight - 4;
}

/**
 * Whether the Sign button is enabled. Every agreement needs consent and a
 * typed name; an uploaded PDF (and any Exhibit A) also needs the reader to
 * have scrolled to the last page first.
 */
export function signEnabled({ needsScroll = false, scrolledToEnd = false, consent = false, name = "", busy = false } = {}) {
  if (busy || consent !== true || String(name).trim().length < 2) return false;
  return needsScroll ? scrolledToEnd === true : true;
}

/** The hash the Sign request carries: the PDF's for an upload, the text's
 *  for a template agreement. */
export function signPayload(doc, { name, consent }) {
  const base = { typed_name: name, consent };
  return doc?.kind === "upload" ? { ...base, file_sha256: doc.file_sha256 }
    : { ...base, rendered_text_sha256: doc?.rendered_text_sha256 };
}

/** Page image URLs for the in-app viewer (document pages, then Exhibit A). */
export function viewerPages(dealId, doc) {
  const out = [];
  const base = `/api/deal-room/deals/${dealId}/agreements/${doc?.id}/pages`;
  if (doc?.kind === "upload") {
    for (let i = 1; i <= (doc.page_count || 0); i += 1) out.push({ src: `${base}/${i}?part=document`, label: `Page ${i}` });
  }
  for (let i = 1; i <= (doc?.exhibit?.page_count || 0); i += 1) {
    out.push({ src: `${base}/${i}?part=exhibit`, label: `Exhibit A — page ${i}` });
  }
  return out;
}

/** A signed agreement line for the investor (room panel and My deal access). */
export function signatureLine(a) {
  if (a.covered_by_master) return `${a.title} — covered by your master NDA signed ${String(a.signed_at).slice(0, 10)}`;
  const v = a.signed_version ?? a.version;
  return `${a.title}${v ? ` (v${v})` : ""} — signed ${String(a.signed_at).slice(0, 10)}`;
}

/** Manager controls for one agreement: what the server lets them do. */
export function agreementControls(envelope, a) {
  if (!canWrite(envelope)) return [];
  const out = [];
  if (a.status === "draft") out.push("publish");
  if (a.status === "published" && !a.virtual) {
    out.push(a.kind === "upload" ? "replace_file" : "new_version");
    if (!a.is_master) out.push("make_master");
  }
  return out;
}


/** Library row controls: none unless the envelope says can_write === true,
 *  then exactly the row's own server flags. */
export function libraryActions(envelope, row) {
  if (!canWrite(envelope) || !row) return [];
  const out = [];
  if (row.can_copy === true) out.push("copy");
  if (row.can_edit === true) out.push("edit");
  if (row.can_publish === true) out.push("publish");
  if (row.can_add_version === true) out.push("add_version");
  return out;
}
