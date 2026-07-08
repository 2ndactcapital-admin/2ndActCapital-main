"use client";

import { useEffect, useRef, useState } from "react";
import { ReferenceSelect } from "@/components/ReferenceSelect";

const DOC_STATUS_CONFIG = {
  active: { label: "Active", bg: "#E8F5E9", text: "#2D6A4F" },
  deprecated: { label: "Deprecated", bg: "#F5F1EB", text: "#64748B" },
  archived: { label: "Archived", bg: "#FEF3F2", text: "#9B2335" },
  deleted: { label: "Deleted", bg: "#FEF3F2", text: "#9B2335" },
};

function formatBytes(n) {
  if (!n) return "";
  if (n < 1024) return `${n} B`;
  if (n < 1048576) return `${Math.round(n / 1024)} KB`;
  return `${(n / 1048576).toFixed(1)} MB`;
}

function shortDate(str) {
  if (!str) return "";
  return new Date(str).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

function StatusPill({ status }) {
  const cfg = DOC_STATUS_CONFIG[status] || { label: status, bg: "#F5F1EB", text: "#64748B" };
  return (
    <span
      className="rounded-full px-2 py-0.5 text-[10px] font-medium"
      style={{ backgroundColor: cfg.bg, color: cfg.text }}
    >
      {cfg.label}
    </span>
  );
}

const FIELD_CLASS =
  "mt-1 w-full rounded-md border border-border bg-bg-card px-3 py-2 text-sm text-text-primary outline-none focus:ring-2 focus:ring-navy";

export default function DocumentsTab({ entityId }) {
  const [docs, setDocs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showAll, setShowAll] = useState(false);
  const [showUpload, setShowUpload] = useState(false);
  const [versionTarget, setVersionTarget] = useState(null);
  const [deleteTarget, setDeleteTarget] = useState(null);
  const [uploading, setUploading] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState(null);
  const uploadRef = useRef(null);
  const versionRef = useRef(null);

  async function loadDocs() {
    setLoading(true);
    try {
      const res = await fetch(`/api/entities/${entityId}/documents`, { cache: "no-store" });
      if (res.ok) {
        const data = await res.json();
        setDocs(data.items || []);
      }
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { loadDocs(); }, [entityId]);

  async function handleUpload(e) {
    e.preventDefault();
    const fd = new FormData(uploadRef.current);
    const file = fd.get("file");
    const title = (fd.get("title") || "").trim();
    if (!file?.name) return setError("Please select a file.");
    if (!title) return setError("Title is required.");
    setUploading(true);
    setError(null);
    try {
      const res = await fetch(`/api/entities/${entityId}/documents`, { method: "POST", body: fd });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) return setError(data.error || data.detail || "Upload failed.");
      setShowUpload(false);
      uploadRef.current?.reset();
      await loadDocs();
    } catch (err) {
      setError(err.message);
    } finally {
      setUploading(false);
    }
  }

  async function handleVersion(e) {
    e.preventDefault();
    const fd = new FormData(versionRef.current);
    const file = fd.get("file");
    if (!file?.name) return setError("Please select a file.");
    setUploading(true);
    setError(null);
    try {
      const res = await fetch(
        `/api/entities/${entityId}/documents/${versionTarget.id}/version`,
        { method: "POST", body: fd },
      );
      const data = await res.json().catch(() => ({}));
      if (!res.ok) return setError(data.error || data.detail || "Upload failed.");
      setVersionTarget(null);
      versionRef.current?.reset();
      await loadDocs();
    } catch (err) {
      setError(err.message);
    } finally {
      setUploading(false);
    }
  }

  async function handleDownload(doc) {
    try {
      const res = await fetch(`/api/entities/${entityId}/documents/${doc.id}/download`);
      const data = await res.json().catch(() => ({}));
      if (res.ok && data.url) window.open(data.url, "_blank", "noopener");
    } catch {}
  }

  async function handleStatusChange(doc, status) {
    try {
      await fetch(`/api/entities/${entityId}/documents/${doc.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status }),
      });
      await loadDocs();
    } catch {}
  }

  async function handleDelete() {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await fetch(`/api/entities/${entityId}/documents/${deleteTarget.id}`, { method: "DELETE" });
      setDeleteTarget(null);
      await loadDocs();
    } catch {}
    setDeleting(false);
  }

  const visible = showAll
    ? docs.filter((d) => d.status !== "deleted")
    : docs.filter((d) => d.status === "active");

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <h2 className="text-base font-semibold text-navy">Documents</h2>
          <label className="flex cursor-pointer items-center gap-1.5 text-xs text-text-muted">
            <input
              type="checkbox"
              checked={showAll}
              onChange={(e) => setShowAll(e.target.checked)}
            />
            Show all versions
          </label>
        </div>
        <button
          type="button"
          onClick={() => { setShowUpload(true); setError(null); }}
          className="rounded-md px-4 py-2 text-sm font-medium text-white"
          style={{ backgroundColor: "#1B2B4B" }}
        >
          Upload document
        </button>
      </div>

      {loading ? (
        <p className="text-sm text-text-muted">Loading…</p>
      ) : visible.length === 0 ? (
        <p className="text-sm text-text-muted">No documents uploaded.</p>
      ) : (
        <ul className="space-y-2">
          {visible.map((doc) => (
            <li key={doc.id} className="rounded-lg border border-border bg-bg-card p-4">
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className="truncate text-sm font-medium text-navy">{doc.title}</span>
                    <span className="font-mono text-[10px] text-text-muted">v{doc.version}</span>
                    <StatusPill status={doc.status} />
                  </div>
                  <div className="mt-1 flex flex-wrap items-center gap-1.5 text-xs text-text-muted">
                    {doc.doc_category && <span>{doc.doc_category}</span>}
                    {doc.file_size ? (
                      <><span>·</span><span>{formatBytes(doc.file_size)}</span></>
                    ) : null}
                    {doc.created_at ? (
                      <><span>·</span><span>{shortDate(doc.created_at)}</span></>
                    ) : null}
                  </div>
                  {doc.tags && doc.tags.length > 0 && (
                    <div className="mt-1.5 flex flex-wrap gap-1">
                      {doc.tags.map((tag) => (
                        <span
                          key={tag}
                          className="rounded-full px-2 py-0.5 text-[10px] font-medium"
                          style={{ backgroundColor: "#E8D5A3", color: "#1B2B4B" }}
                        >
                          {tag}
                        </span>
                      ))}
                    </div>
                  )}
                </div>

                {/* Row actions */}
                <div className="flex shrink-0 flex-wrap items-center gap-3">
                  <button
                    type="button"
                    onClick={() => handleDownload(doc)}
                    className="text-xs font-medium text-navy hover:underline"
                  >
                    Download
                  </button>
                  {doc.status === "active" && (
                    <>
                      <button
                        type="button"
                        onClick={() => { setVersionTarget(doc); setError(null); }}
                        className="text-xs font-medium text-text-secondary hover:underline"
                      >
                        New version
                      </button>
                      <button
                        type="button"
                        onClick={() => handleStatusChange(doc, "archived")}
                        className="text-xs text-text-muted hover:text-[#9B2335]"
                      >
                        Archive
                      </button>
                    </>
                  )}
                  {(doc.status === "archived" || doc.status === "deprecated") && (
                    <button
                      type="button"
                      onClick={() => handleStatusChange(doc, "active")}
                      className="text-xs text-text-muted hover:text-navy"
                    >
                      Restore
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={() => setDeleteTarget(doc)}
                    className="text-xs text-[#9B2335] hover:underline"
                  >
                    Delete
                  </button>
                </div>
              </div>
            </li>
          ))}
        </ul>
      )}

      {/* Upload modal */}
      {showUpload && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30">
          <div className="w-full max-w-md rounded-lg border border-border bg-white p-6 shadow-lg">
            <h3 className="font-semibold text-navy" style={{ fontFamily: "Spectral, Georgia, serif" }}>
              Upload document
            </h3>
            <form ref={uploadRef} onSubmit={handleUpload} className="mt-4 space-y-3">
              <div>
                <label className="block text-xs font-medium uppercase tracking-wide text-text-muted">Title *</label>
                <input name="title" required placeholder="e.g. Subscription Agreement" className={FIELD_CLASS} />
              </div>
              <div>
                <label className="block text-xs font-medium uppercase tracking-wide text-text-muted">Category</label>
                <ReferenceSelect listKey="doc_category" name="doc_category" placeholder="Select category…" />
              </div>
              <div>
                <label className="block text-xs font-medium uppercase tracking-wide text-text-muted">Tags (comma-separated)</label>
                <input name="tags" placeholder="e.g. signed, 2024" className={FIELD_CLASS} />
              </div>
              <div>
                <label className="block text-xs font-medium uppercase tracking-wide text-text-muted">File *</label>
                <input
                  name="file"
                  type="file"
                  required
                  className="mt-1 w-full text-sm text-text-secondary file:mr-3 file:rounded file:border-0 file:bg-bg-app file:px-3 file:py-1 file:text-xs file:font-medium"
                />
              </div>
              {error && <p className="text-sm text-[#9B2335]">{error}</p>}
              <div className="flex justify-end gap-2 pt-2">
                <button
                  type="button"
                  onClick={() => { setShowUpload(false); setError(null); }}
                  className="rounded-md border border-border px-4 py-2 text-sm font-medium text-text-secondary hover:bg-bg-app"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={uploading}
                  className="rounded-md px-4 py-2 text-sm font-medium text-white disabled:opacity-60"
                  style={{ backgroundColor: "#1B2B4B" }}
                >
                  {uploading ? "Uploading…" : "Upload"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Add version modal */}
      {versionTarget && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30">
          <div className="w-full max-w-md rounded-lg border border-border bg-white p-6 shadow-lg">
            <h3 className="font-semibold text-navy" style={{ fontFamily: "Spectral, Georgia, serif" }}>
              Add new version
            </h3>
            <p className="mt-1 text-sm text-text-secondary">The current version will be marked as deprecated.</p>
            <p className="mt-0.5 text-xs text-text-muted">{versionTarget.title}</p>
            <form ref={versionRef} onSubmit={handleVersion} className="mt-4 space-y-3">
              <div>
                <label className="block text-xs font-medium uppercase tracking-wide text-text-muted">Replacement file *</label>
                <input
                  name="file"
                  type="file"
                  required
                  className="mt-1 w-full text-sm text-text-secondary file:mr-3 file:rounded file:border-0 file:bg-bg-app file:px-3 file:py-1 file:text-xs file:font-medium"
                />
              </div>
              {error && <p className="text-sm text-[#9B2335]">{error}</p>}
              <div className="flex justify-end gap-2 pt-2">
                <button
                  type="button"
                  onClick={() => { setVersionTarget(null); setError(null); }}
                  className="rounded-md border border-border px-4 py-2 text-sm font-medium text-text-secondary hover:bg-bg-app"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={uploading}
                  className="rounded-md px-4 py-2 text-sm font-medium text-white disabled:opacity-60"
                  style={{ backgroundColor: "#1B2B4B" }}
                >
                  {uploading ? "Uploading…" : "Upload version"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Delete confirm modal */}
      {deleteTarget && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30">
          <div className="w-full max-w-sm rounded-lg border border-border bg-white p-6 shadow-lg">
            <h3 className="font-semibold text-navy" style={{ fontFamily: "Spectral, Georgia, serif" }}>
              Delete document?
            </h3>
            <p className="mt-2 text-sm text-text-secondary">
              <strong className="font-medium">{deleteTarget.title}</strong> will be permanently removed from storage. This cannot be undone.
            </p>
            <div className="mt-4 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setDeleteTarget(null)}
                className="rounded-md border border-border px-4 py-2 text-sm font-medium text-text-secondary hover:bg-bg-app"
              >
                Cancel
              </button>
              <button
                type="button"
                disabled={deleting}
                onClick={handleDelete}
                className="rounded-md px-4 py-2 text-sm font-medium text-white disabled:opacity-60"
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
