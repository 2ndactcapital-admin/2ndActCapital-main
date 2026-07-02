"use client";

import { useRef, useState } from "react";
import { formatDate } from "@/lib/format";

const DOC_TYPES = [
  { value: "formation", label: "Formation" },
  { value: "subscription", label: "Subscription Agreement" },
  { value: "side_letter", label: "Side Letter" },
  { value: "other", label: "Other" },
];

const STATUS_CFG = {
  active: { label: "Active", bg: "#E8F5E9", color: "#2D6A4F" },
  deprecated: { label: "Deprecated", bg: "#F5F1EB", color: "#64748B" },
  archived: { label: "Archived", bg: "#FEF3F2", color: "#9B2335" },
};

function extractErrorMessage(err) {
  if (!err) return "Upload failed";
  if (typeof err === "string") return err;
  if (Array.isArray(err)) {
    const first = err[0];
    if (first?.msg) {
      const loc = Array.isArray(first.loc) ? first.loc.join(" → ") : "";
      return loc ? `${loc}: ${first.msg}` : first.msg;
    }
    return String(err);
  }
  if (err.detail) return extractErrorMessage(err.detail);
  if (err.message) return err.message;
  return String(err);
}

function StatusPill({ status }) {
  const cfg = STATUS_CFG[status] || { label: status, bg: "#F5F1EB", color: "#64748B" };
  return (
    <span
      className="rounded-full px-2 py-0.5 text-[10px] font-medium capitalize"
      style={{ background: cfg.bg, color: cfg.color }}
    >
      {cfg.label}
    </span>
  );
}

export default function SPVDocumentsTab({ spvId, initialDocuments = [], staff = false }) {
  const [documents, setDocuments] = useState(
    initialDocuments.filter((d) => d.status !== "deleted"),
  );
  const [showAll, setShowAll] = useState(false);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [versionTarget, setVersionTarget] = useState(null);
  const [deleteTarget, setDeleteTarget] = useState(null);
  const [uploading, setUploading] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [uploadError, setUploadError] = useState(null);
  const [docType, setDocType] = useState("formation");
  const [title, setTitle] = useState("");
  const fileRef = useRef(null);
  const versionFileRef = useRef(null);

  async function refreshDocs() {
    const res = await fetch(`/api/spvs/${spvId}/documents`, { cache: "no-store" });
    if (res.ok) {
      const updated = await res.json();
      setDocuments((Array.isArray(updated) ? updated : []).filter((d) => d.status !== "deleted"));
    }
  }

  async function handleUpload(e) {
    e.preventDefault();
    const file = fileRef.current?.files?.[0];
    if (!file) { setUploadError("Please select a file."); return; }
    setUploading(true);
    setUploadError(null);

    const fd = new FormData();
    fd.append("file", file);
    fd.append("document_type", docType);
    fd.append("title", title || file.name);

    try {
      const res = await fetch(`/api/spvs/${spvId}/documents`, { method: "POST", body: fd });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw extractErrorMessage(data.error ?? data.detail ?? data);
      await refreshDocs();
      setUploadOpen(false);
      setTitle("");
      setDocType("formation");
      if (fileRef.current) fileRef.current.value = "";
    } catch (err) {
      setUploadError(typeof err === "string" ? err : extractErrorMessage(err));
    } finally {
      setUploading(false);
    }
  }

  async function handleVersion(e) {
    e.preventDefault();
    const file = versionFileRef.current?.files?.[0];
    if (!file) { setUploadError("Please select a file."); return; }
    setUploading(true);
    setUploadError(null);

    const fd = new FormData();
    fd.append("file", file);
    fd.append("document_type", versionTarget.document_type || "general");

    try {
      const res = await fetch(
        `/api/spvs/${spvId}/documents/${versionTarget.id}/version`,
        { method: "POST", body: fd },
      );
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw extractErrorMessage(data.error ?? data.detail ?? data);
      await refreshDocs();
      setVersionTarget(null);
      if (versionFileRef.current) versionFileRef.current.value = "";
    } catch (err) {
      setUploadError(typeof err === "string" ? err : extractErrorMessage(err));
    } finally {
      setUploading(false);
    }
  }

  async function handleDownload(doc) {
    try {
      const res = await fetch(`/api/spvs/${spvId}/documents/${doc.id}/download`);
      const data = await res.json().catch(() => ({}));
      if (res.ok && data.url) window.open(data.url, "_blank", "noopener");
    } catch {}
  }

  async function handleStatusChange(doc, status) {
    try {
      await fetch(`/api/spvs/${spvId}/documents/${doc.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status }),
      });
      await refreshDocs();
    } catch {}
  }

  async function handleDelete() {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await fetch(`/api/spvs/${spvId}/documents/${deleteTarget.id}`, { method: "DELETE" });
      setDeleteTarget(null);
      await refreshDocs();
    } catch {}
    setDeleting(false);
  }

  const visible = showAll
    ? documents
    : documents.filter((d) => d.status === "active");

  return (
    <div>
      {/* Toolbar */}
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <h2 className="text-base font-semibold text-[#1B2B4B]">Documents</h2>
          {staff && (
            <label className="flex cursor-pointer items-center gap-1.5 text-xs text-[#64748B]">
              <input
                type="checkbox"
                checked={showAll}
                onChange={(e) => setShowAll(e.target.checked)}
              />
              Show all versions
            </label>
          )}
        </div>
        {staff && (
          <button
            type="button"
            onClick={() => { setUploadOpen(true); setUploadError(null); }}
            className="rounded-md px-4 py-2 text-sm font-medium text-white"
            style={{ backgroundColor: "#1B2B4B" }}
          >
            Upload Document
          </button>
        )}
      </div>

      {/* Document list */}
      {visible.length === 0 ? (
        <p className="py-6 text-center text-sm text-[#64748B]">No documents uploaded.</p>
      ) : (
        <ul className="divide-y divide-[#E2E8F0]">
          {visible.map((d) => (
            <li key={d.id} className="flex flex-wrap items-center justify-between gap-3 py-3">
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium text-[#0F172A]">{d.file_name || d.title}</p>
                <p className="text-xs text-[#64748B]">
                  {d.document_type} · {formatDate(d.created_at)}
                </p>
              </div>
              <div className="flex shrink-0 flex-wrap items-center gap-3">
                <StatusPill status={d.status} />
                <button
                  type="button"
                  onClick={() => handleDownload(d)}
                  className="text-xs font-medium text-[#1B2B4B] hover:underline"
                >
                  Download
                </button>
                {staff && d.status === "active" && (
                  <>
                    <button
                      type="button"
                      onClick={() => { setVersionTarget(d); setUploadError(null); }}
                      className="text-xs text-[#64748B] hover:text-[#0F172A]"
                    >
                      New version
                    </button>
                    <button
                      type="button"
                      onClick={() => handleStatusChange(d, "archived")}
                      className="text-xs text-[#64748B] hover:text-[#9B2335]"
                    >
                      Archive
                    </button>
                  </>
                )}
                {staff && (d.status === "archived" || d.status === "deprecated") && (
                  <button
                    type="button"
                    onClick={() => handleStatusChange(d, "active")}
                    className="text-xs text-[#64748B] hover:text-[#1B2B4B]"
                  >
                    Restore
                  </button>
                )}
                {staff && (
                  <button
                    type="button"
                    onClick={() => setDeleteTarget(d)}
                    className="text-xs text-[#9B2335] hover:underline"
                  >
                    Delete
                  </button>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}

      {/* Upload modal */}
      {uploadOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
          <div className="w-full max-w-sm rounded-xl bg-white p-6 shadow-lg">
            <h2
              className="mb-4 text-base font-light"
              style={{ fontFamily: "Spectral, Georgia, serif", color: "#1B2B4B" }}
            >
              Upload Document
            </h2>
            <form onSubmit={handleUpload} className="space-y-3">
              <div>
                <label className="mb-1 block text-xs font-medium text-[#334155]">Document Type</label>
                <select
                  value={docType}
                  onChange={(e) => setDocType(e.target.value)}
                  className="w-full rounded border border-[#E2E8F0] bg-white px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-[#C5A880]"
                >
                  {DOC_TYPES.map((t) => (
                    <option key={t.value} value={t.value}>{t.label}</option>
                  ))}
                </select>
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium text-[#334155]">
                  Title (optional — defaults to file name)
                </label>
                <input
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                  placeholder="e.g. Series A Subscription Agreement"
                  className="w-full rounded border border-[#E2E8F0] px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-[#C5A880]"
                />
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium text-[#334155]">File *</label>
                <input
                  ref={fileRef}
                  type="file"
                  required
                  accept=".pdf,.doc,.docx,.xls,.xlsx,.png,.jpg"
                  className="w-full text-sm text-[#334155] file:mr-3 file:rounded file:border-0 file:bg-[#F5F1EB] file:px-3 file:py-1.5 file:text-xs file:font-medium file:text-[#1B2B4B]"
                />
              </div>
              {uploadError && <p className="text-xs text-[#9B2335]">{uploadError}</p>}
              <div className="flex justify-end gap-3 pt-1">
                <button
                  type="button"
                  onClick={() => { setUploadOpen(false); setUploadError(null); }}
                  className="rounded-md px-4 py-2 text-sm text-[#64748B] hover:text-[#0F172A]"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={uploading}
                  className="rounded-md px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
                  style={{ backgroundColor: "#1B2B4B" }}
                >
                  {uploading ? "Uploading…" : "Upload"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* New version modal */}
      {versionTarget && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
          <div className="w-full max-w-sm rounded-xl bg-white p-6 shadow-lg">
            <h2
              className="mb-1 text-base font-light"
              style={{ fontFamily: "Spectral, Georgia, serif", color: "#1B2B4B" }}
            >
              New Version
            </h2>
            <p className="mb-4 text-sm text-[#64748B]">
              {versionTarget.file_name} — current version will be marked deprecated.
            </p>
            <form onSubmit={handleVersion} className="space-y-3">
              <div>
                <label className="mb-1 block text-xs font-medium text-[#334155]">Replacement file *</label>
                <input
                  ref={versionFileRef}
                  type="file"
                  required
                  accept=".pdf,.doc,.docx,.xls,.xlsx,.png,.jpg"
                  className="w-full text-sm text-[#334155] file:mr-3 file:rounded file:border-0 file:bg-[#F5F1EB] file:px-3 file:py-1.5 file:text-xs file:font-medium file:text-[#1B2B4B]"
                />
              </div>
              {uploadError && <p className="text-xs text-[#9B2335]">{uploadError}</p>}
              <div className="flex justify-end gap-3 pt-1">
                <button
                  type="button"
                  onClick={() => { setVersionTarget(null); setUploadError(null); }}
                  className="rounded-md px-4 py-2 text-sm text-[#64748B] hover:text-[#0F172A]"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={uploading}
                  className="rounded-md px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
                  style={{ backgroundColor: "#1B2B4B" }}
                >
                  {uploading ? "Uploading…" : "Upload version"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Delete confirm */}
      {deleteTarget && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
          <div className="w-full max-w-sm rounded-xl bg-white p-6 shadow-lg">
            <h2
              className="mb-2 text-base font-light"
              style={{ fontFamily: "Spectral, Georgia, serif", color: "#1B2B4B" }}
            >
              Delete document?
            </h2>
            <p className="text-sm text-[#64748B]">
              <strong className="font-medium text-[#0F172A]">{deleteTarget.file_name || deleteTarget.title}</strong> will be permanently removed from storage. This cannot be undone.
            </p>
            <div className="mt-5 flex justify-end gap-3">
              <button
                type="button"
                onClick={() => setDeleteTarget(null)}
                className="rounded-md px-4 py-2 text-sm text-[#64748B] hover:text-[#0F172A]"
              >
                Cancel
              </button>
              <button
                type="button"
                disabled={deleting}
                onClick={handleDelete}
                className="rounded-md px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
                style={{ backgroundColor: "#9B2335" }}
              >
                {deleting ? "Deleting…" : "Delete"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
