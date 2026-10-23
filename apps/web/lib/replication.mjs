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

// ── REP4 — the Model tab ────────────────────────────────────────────────────
// Every control below is decided from the Model tab's own envelope
// (GET /replication/registry/{id}/model) and fails CLOSED: Build, Preview and
// "Use this model" need `permissions.can_build === true` (manage_models AND
// view_replication on the server) — a missing envelope shows none. Labels
// (questions, statuses, methods, policies, custodians) come from its
// vocabularies.

/** Build / Preview / Use this model / Check viability. */
export function modelControls(envelope) {
  const p = perms(envelope);
  const canBuild = isTrue(p.can_build);
  const editable = Array.isArray(envelope?.vocabularies?.editable) ? envelope.vocabularies.editable : [];
  return {
    build: canBuild && editable.length > 0,
    preview: canBuild && editable.length > 0,
    use: canBuild,
    viability: isTrue(p.can_read),
    openModel: isTrue(p.can_view_models),
  };
}

/** The registry offer: the existing model for the chosen key, first. */
export function findOffer(envelope, { custodian_code: code, method, top_n: topN } = {}) {
  const models = Array.isArray(envelope?.models) ? envelope.models : [];
  const n = method === "top_n" ? Number(topN) || null : null;
  return models.find((m) => m?.key && m.key.custodian_code === code && m.key.method === method
    && (m.key.top_n ?? null) === n) || null;
}

/** "Holdings of QQQ (v3, built for altruist, full)". */
export function offerText(offer, vocab) {
  if (!offer?.model) return "";
  const v = offer.live_version ? `v${offer.live_version.version_no}` : "no approved version yet";
  const custodian = labelOf(vocab?.custodians, offer.key?.custodian_code);
  const method = offer.key?.method === "top_n" ? `top ${offer.key.top_n}` : labelOf(vocab?.methods, offer.key?.method);
  return `${offer.model.name} (${v}, built for ${custodian}, ${method})`;
}

const STATUS_TONE = { yes: "text-[#2D6A4F]", no: "text-[#9B2335]", unknown: "text-[#64748B]" };

/** The five answers in the server's question order, each with its label and tone. */
export function viabilityRows(report, vocab) {
  const answers = Array.isArray(report?.answers) ? report.answers : [];
  const order = Array.isArray(vocab?.questions) ? vocab.questions.map((q) => q.key) : answers.map((a) => a.key);
  return order.map((key) => answers.find((a) => a.key === key)).filter(Boolean).map((a) => ({
    key: a.key,
    question: labelOf(vocab?.questions, a.key) || a.label,
    status: a.status,
    statusLabel: labelOf(vocab?.answer_statuses, a.status),
    tone: STATUS_TONE[a.status] || STATUS_TONE.unknown,
    reason: a.reason || "",
    missing: Array.isArray(a.missing) ? a.missing : [],
  }));
}

/** Cost lines: an unknown is shown as unknown, the tax line never as a number. */
export function costRows(report, vocab) {
  const lines = Array.isArray(report?.cost_lines) ? report.cost_lines : [];
  return lines.map((l) => ({
    key: l.key,
    label: labelOf(vocab?.cost_lines, l.key) || l.label,
    amount: l.key === "tax_benefit" ? (vocab?.tax_line || l.reason || "")
      : (l.status === "known" && l.amount != null ? `$${Number(l.amount).toLocaleString("en-US", {
        minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : labelOf(vocab?.answer_statuses, "unknown") || "Unknown"),
    reason: l.reason || "",
    unknown: l.status !== "known",
  }));
}

/** Build-form body from the form state: percent strings pass through untouched. */
export function buildBody(form) {
  const body = { custodian_code: form.custodian_code, method: form.method,
    unresolved_policy: form.unresolved_policy };
  if (form.method === "top_n") body.top_n = Number(form.top_n);
  for (const k of ["min_weight_pct", "cash_target_pct", "drift_threshold_pct", "turnover_review_threshold_pct",
    "rebalance_frequency", "rebalance_reason", "description"]) {
    if (form[k] !== undefined && form[k] !== null && String(form[k]).trim() !== "") body[k] = String(form[k]).trim();
  }
  return body;
}
