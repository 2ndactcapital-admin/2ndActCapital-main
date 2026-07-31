"use client";

// Chancery Phase 10 (Task 4) — minimal VDR intake + proposal review (client).
//
// Two panels, deliberately simple:
//   1. Upload — reuses the existing Chancery intake endpoint with the new
//      `is_vdr` flag. When checked, the whole drop is read TOGETHER and, if the
//      documents confidently describe one deal, a pending proposal appears below.
//   2. Pending proposals — approve (creates a REAL deal via the same createDeal
//      core the marketplace uses + links every document) or decline.
//
// Rule 5: calls the Next API routes, never FastAPI directly. No hardcoded
// display data beyond the fixed proposed-field labels this feature owns.

import { useCallback, useEffect, useState } from "react";
import { formatDate } from "@/lib/format";

const FIELD_LABELS = [
  ["name", "Deal name"],
  ["description", "Thesis / summary"],
  ["sponsor_name_override", "Sponsor"],
  ["asset_class_hint", "Asset class (hint)"],
  ["location", "Location"],
  ["target_raise", "Target raise"],
  ["minimum_investment", "Minimum investment"],
  ["expected_return_pct", "Expected return %"],
  ["term_months", "Term (months)"],
];

function money(v) {
  if (v === null || v === undefined || v === "") return null;
  const n = Number(v);
  return Number.isFinite(n) ? `$${n.toLocaleString()}` : String(v);
}

function ProposalCard({ p, onApprove, onReject, busy }) {
  const f = p.proposed_fields || {};
  const rows = FIELD_LABELS.map(([key, label]) => {
    let val = f[key];
    if (key === "target_raise" || key === "minimum_investment") val = money(val);
    if (Array.isArray(val)) val = val.join(", ");
    if (val === null || val === undefined || val === "") return null;
    return (
      <div key={key} className="flex gap-2 py-1 text-sm">
        <span className="w-40 shrink-0 text-text-muted">{label}</span>
        <span className="text-text-primary">{String(val)}</span>
      </div>
    );
  }).filter(Boolean);

  return (
    <li className="rounded-lg border border-border bg-bg-card p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-base font-semibold text-navy">
            {f.name || "Untitled proposed deal"}
          </p>
          <p className="mt-0.5 text-xs text-text-muted">
            {f.source_document_count
              ? `${f.source_document_count} documents · `
              : ""}
            confidence: {f.confidence || "—"}
            {p.created_at ? ` · ${formatDate(p.created_at)}` : ""}
          </p>
        </div>
        <div className="flex shrink-0 gap-2">
          <button
            type="button"
            disabled={busy}
            onClick={() => onApprove(p)}
            className="rounded-md bg-navy px-3 py-1.5 text-sm font-medium text-bg-app hover:opacity-90 disabled:opacity-60"
          >
            Approve &amp; create deal
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={() => onReject(p)}
            className="rounded-md border border-border px-3 py-1.5 text-sm font-medium text-text-primary hover:bg-bg-app disabled:opacity-60"
          >
            Decline
          </button>
        </div>
      </div>
      <div className="mt-3 border-t border-border pt-3">{rows}</div>
      {f.rationale ? (
        <p className="mt-2 text-xs italic text-text-muted">{f.rationale}</p>
      ) : null}
    </li>
  );
}

export default function VDRProposalsManager() {
  const [proposals, setProposals] = useState(null); // null = loading
  const [error, setError] = useState(null);
  const [busyId, setBusyId] = useState(null);

  // upload state
  const [files, setFiles] = useState(null);
  const [isVdr, setIsVdr] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [notice, setNotice] = useState(null);

  const load = useCallback(async () => {
    try {
      const res = await fetch("/api/vdr-proposals", { cache: "no-store" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(data.error || "Could not load proposals.");
        setProposals([]);
      } else {
        setProposals(Array.isArray(data.proposals) ? data.proposals : []);
        setError(null);
      }
    } catch (err) {
      setError(err.message || "Could not load proposals.");
      setProposals([]);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function onUpload(e) {
    e.preventDefault();
    if (!files || files.length === 0) {
      setNotice({ kind: "error", text: "Choose at least one file." });
      return;
    }
    setUploading(true);
    setNotice(null);
    try {
      const form = new FormData();
      for (const file of files) form.append("files", file);
      form.append("is_vdr", isVdr ? "true" : "false");
      const res = await fetch("/api/documents", { method: "POST", body: form });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setNotice({ kind: "error", text: data.error || "Upload failed." });
      } else {
        const v = data.vdr_analysis;
        if (isVdr && v) {
          setNotice({
            kind: v.proposal_created ? "success" : "info",
            text: v.proposal_created
              ? "Deal proposal created from the data room — review it below."
              : `No proposal created: ${v.reason || "the documents did not clearly describe one deal."}`,
          });
        } else {
          setNotice({
            kind: "success",
            text: `Uploaded ${data.file_count} document(s).`,
          });
        }
        setFiles(null);
        e.target.reset();
        await load();
      }
    } catch (err) {
      setNotice({ kind: "error", text: err.message || "Upload failed." });
    } finally {
      setUploading(false);
    }
  }

  async function act(p, action) {
    setBusyId(p.id);
    setError(null);
    try {
      const res = await fetch(`/api/vdr-proposals/${p.id}/${action}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(data.error || `Could not ${action} the proposal.`);
      } else if (action === "approve") {
        setNotice({
          kind: "success",
          text: `Deal created${data.deal_name ? `: ${data.deal_name}` : ""} · ${data.linked_documents ?? 0} document(s) linked.`,
        });
      } else {
        setNotice({ kind: "info", text: "Proposal declined." });
      }
      await load();
    } catch (err) {
      setError(err.message || `Could not ${action} the proposal.`);
    } finally {
      setBusyId(null);
    }
  }

  const noticeColor =
    notice?.kind === "error"
      ? "text-[#9B2335]"
      : notice?.kind === "success"
        ? "text-[#2D6A4F]"
        : "text-text-muted";

  return (
    <div className="mt-6 space-y-8">
      {/* Upload panel */}
      <section className="rounded-lg border border-border bg-bg-card p-4">
        <h2 className="text-lg font-semibold text-navy">Upload a data room</h2>
        <form onSubmit={onUpload} className="mt-3 space-y-3">
          <input
            type="file"
            multiple
            onChange={(e) => setFiles(e.target.files)}
            className="block w-full text-sm text-text-primary file:mr-3 file:rounded-md file:border-0 file:bg-navy file:px-3 file:py-1.5 file:text-sm file:text-bg-app"
            aria-label="Select documents to upload"
          />
          <label className="flex items-center gap-2 text-sm text-text-primary">
            <input
              type="checkbox"
              checked={isVdr}
              onChange={(e) => setIsVdr(e.target.checked)}
            />
            This is a VDR for a new deal (analyze the documents together)
          </label>
          <button
            type="submit"
            disabled={uploading}
            className="rounded-md bg-navy px-4 py-2 text-sm font-medium text-bg-app hover:opacity-90 disabled:opacity-60"
          >
            {uploading ? "Uploading…" : "Upload"}
          </button>
        </form>
        {notice ? <p className={`mt-3 text-sm ${noticeColor}`}>{notice.text}</p> : null}
      </section>

      {/* Proposals panel */}
      <section>
        <h2 className="text-lg font-semibold text-navy">Pending proposals</h2>
        <div className="mt-3">
          {error ? (
            <p className="text-sm text-[#9B2335]">{error}</p>
          ) : proposals === null ? (
            <p className="text-sm text-text-muted">Loading…</p>
          ) : proposals.length === 0 ? (
            <p className="text-sm text-text-muted">
              No pending proposals. Upload a data room above to create one.
            </p>
          ) : (
            <ul className="space-y-3">
              {proposals.map((p) => (
                <ProposalCard
                  key={p.id}
                  p={p}
                  busy={busyId === p.id}
                  onApprove={(pp) => act(pp, "approve")}
                  onReject={(pp) => act(pp, "reject")}
                />
              ))}
            </ul>
          )}
        </div>
      </section>
    </div>
  );
}
