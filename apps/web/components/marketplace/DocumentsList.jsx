"use client";

import { useActionState, useEffect, useRef, useState } from "react";
import {
  IconFileText,
  IconFileSpreadsheet,
  IconFile,
  IconUpload,
} from "@tabler/icons-react";
import {
  uploadDocumentAction,
  reviewDocumentAction,
} from "@/lib/marketplaceActions";
import { humanize, formatDate } from "@/lib/format";

const PROC_COLORS = {
  pending: "bg-border text-text-secondary",
  processing: "bg-[#DBEAFE] text-[#1E40AF] animate-pulse",
  extracted: "bg-[#DCFCE7] text-[#166534]",
  failed: "bg-[#FBE3E6] text-[#9B2335]",
};

const REVIEW_STATUS_COLORS = {
  pending: "bg-border text-text-secondary",
  under_review: "bg-[#DBEAFE] text-[#1E40AF]",
  approved: "bg-[#DCFCE7] text-[#166534]",
  rejected: "bg-[#FBE3E6] text-[#9B2335]",
};

function FileIcon({ type }) {
  const t = (type || "").toLowerCase();
  if (t.includes("pdf"))
    return <IconFileText size={20} className="text-[#9B2335]" />;
  if (t.includes("sheet") || t.includes("excel") || t.includes("csv"))
    return <IconFileSpreadsheet size={20} className="text-[#166534]" />;
  return <IconFile size={20} className="text-text-muted" />;
}

function DocReviewForm({ dealId, doc, documentStatuses, onUpdate }) {
  const [open, setOpen] = useState(false);
  const [state, formAction, pending] = useActionState(
    reviewDocumentAction.bind(null, dealId, doc.id),
    {},
  );

  useEffect(() => {
    if (state?.ok && state.item) {
      onUpdate(state.item);
      setOpen(false);
    }
  }, [state]);

  const reviewStatusColor =
    REVIEW_STATUS_COLORS[doc.status] || REVIEW_STATUS_COLORS.pending;

  return (
    <div className="mt-2">
      <div className="flex items-center gap-2">
        <span
          className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${reviewStatusColor}`}
        >
          {documentStatuses.find((s) => s.config_key === doc.status)
            ?.config_value || humanize(doc.status || "pending")}
        </span>
        {doc.visible_to_members && doc.status === "approved" && (
          <span className="text-xs text-[#166534]">Visible to members</span>
        )}
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="text-xs font-medium text-navy hover:underline"
        >
          {open ? "Cancel" : "Review"}
        </button>
      </div>

      {open && (
        <form action={formAction} className="mt-2 space-y-2 rounded-md border border-border bg-bg-app p-3">
          <div>
            <label className="block text-xs font-medium text-text-muted mb-1">
              Status
            </label>
            <select
              name="status"
              defaultValue={doc.status || "pending"}
              className="w-full rounded-md border border-border bg-bg-card px-2 py-1.5 text-sm text-text-primary"
            >
              {documentStatuses.map((s) => (
                <option key={s.config_key} value={s.config_key}>
                  {s.config_value || s.config_key}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-xs font-medium text-text-muted mb-1">
              Review Notes
            </label>
            <input
              name="review_notes"
              defaultValue={doc.review_notes || ""}
              placeholder="Optional notes…"
              className="w-full rounded-md border border-border bg-bg-card px-2 py-1.5 text-sm text-text-primary outline-none focus:ring-2 focus:ring-navy"
            />
          </div>
          <label className="flex items-center gap-2 text-sm text-text-secondary">
            <input
              type="checkbox"
              name="visible_to_members"
              value="on"
              defaultChecked={doc.visible_to_members}
              className="rounded"
            />
            Visible to members
          </label>
          {state?.error && (
            <p className="text-xs text-[#9B2335]">{state.error}</p>
          )}
          <button
            type="submit"
            disabled={pending}
            className="rounded-md bg-navy px-3 py-1.5 text-xs font-medium text-bg-app hover:opacity-90 disabled:opacity-60"
          >
            {pending ? "Saving…" : "Save Review"}
          </button>
        </form>
      )}
    </div>
  );
}

export default function DocumentsList({
  dealId,
  initial = [],
  canUpload = false,
  canReview = false,
  documentStatuses = [],
}) {
  const [docs, setDocs] = useState(initial);
  const [uploading, setUploading] = useState(false);
  const formRef = useRef(null);
  const [state, formAction, pending] = useActionState(
    uploadDocumentAction.bind(null, dealId),
    {},
  );

  useEffect(() => {
    if (state?.ok && state.item) {
      setDocs((prev) =>
        prev.some((d) => d.id === state.item.id)
          ? prev
          : [state.item, ...prev],
      );
      formRef.current?.reset();
      setUploading(false);
    }
  }, [state]);

  function handleDocUpdate(updatedDoc) {
    setDocs((prev) => prev.map((d) => (d.id === updatedDoc.id ? updatedDoc : d)));
  }

  return (
    <section>
      <div className="flex items-center justify-between">
        <h2 className="text-base font-semibold text-navy">Documents</h2>
        {canUpload && !uploading && (
          <button
            type="button"
            onClick={() => setUploading(true)}
            className="inline-flex items-center gap-1 text-sm font-medium text-navy hover:underline"
          >
            <IconUpload size={16} /> Upload Document
          </button>
        )}
      </div>

      {docs.length === 0 ? (
        <p className="mt-3 text-sm text-text-muted">No documents uploaded.</p>
      ) : (
        <ul className="mt-3 space-y-2">
          {docs.map((d) => (
            <li
              key={d.id}
              className="rounded-lg border border-border bg-bg-card p-3"
            >
              <div className="flex items-center gap-3">
                <FileIcon type={d.file_type} />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium text-text-primary">
                    {d.file_name}
                  </p>
                  <p className="text-xs text-text-muted">
                    {[
                      d.document_type && humanize(d.document_type),
                      d.file_type,
                      formatDate(d.created_at),
                    ]
                      .filter(Boolean)
                      .join(" · ")}
                  </p>
                </div>
                <span
                  className={`shrink-0 rounded-full px-2.5 py-0.5 text-xs font-medium ${
                    PROC_COLORS[d.processing_status] || PROC_COLORS.pending
                  }`}
                >
                  {humanize(d.processing_status)}
                </span>
              </div>
              {canReview && (
                <DocReviewForm
                  dealId={dealId}
                  doc={d}
                  documentStatuses={documentStatuses}
                  onUpdate={handleDocUpdate}
                />
              )}
            </li>
          ))}
        </ul>
      )}

      {uploading && (
        <form
          ref={formRef}
          action={formAction}
          className="mt-4 space-y-3 rounded-lg border border-border bg-bg-card p-4"
        >
          <input
            type="file"
            name="file"
            required
            className="block w-full text-sm text-text-secondary file:mr-3 file:rounded-md file:border-0 file:bg-navy file:px-3 file:py-1.5 file:text-sm file:font-medium file:text-bg-app"
          />
          <input
            name="document_type"
            placeholder="Document type (e.g. PPM, financials)"
            className="w-full rounded-md border border-border bg-bg-card px-3 py-2 text-sm text-text-primary outline-none focus:ring-2 focus:ring-navy"
          />
          {state?.error && (
            <p className="text-sm text-[#9B2335]">{state.error}</p>
          )}
          <div className="flex gap-2">
            <button
              type="submit"
              disabled={pending}
              className="rounded-md bg-navy px-4 py-2 text-sm font-medium text-bg-app hover:opacity-90 disabled:opacity-60"
            >
              {pending ? "Uploading…" : "Upload"}
            </button>
            <button
              type="button"
              onClick={() => setUploading(false)}
              className="rounded-md border border-border px-4 py-2 text-sm font-medium text-text-secondary hover:bg-border"
            >
              Cancel
            </button>
          </div>
        </form>
      )}
    </section>
  );
}
