"use client";

import { useRef, useState } from "react";
import { formatDate } from "@/lib/format";

const DOC_TYPES = [
  { value: "formation", label: "Formation" },
  { value: "subscription", label: "Subscription Agreement" },
  { value: "side_letter", label: "Side Letter" },
  { value: "other", label: "Other" },
];

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

export default function SPVDocumentsTab({ spvId, initialDocuments = [], staff = false }) {
  const [documents, setDocuments] = useState(initialDocuments);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState(null);
  const [docType, setDocType] = useState("formation");
  const [title, setTitle] = useState("");
  const fileRef = useRef(null);

  async function handleUpload(e) {
    e.preventDefault();
    const file = fileRef.current?.files?.[0];
    if (!file) {
      setUploadError("Please select a file.");
      return;
    }
    setUploading(true);
    setUploadError(null);

    const fd = new FormData();
    fd.append("file", file);
    fd.append("document_type", docType);
    fd.append("title", title || file.name);

    try {
      const res = await fetch(`/api/spvs/${spvId}/documents`, {
        method: "POST",
        body: fd,
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw extractErrorMessage(data.error ?? data.detail ?? data);

      // Refresh document list after successful upload.
      const listRes = await fetch(`/api/spvs/${spvId}/documents`, { cache: "no-store" });
      if (listRes.ok) {
        const updated = await listRes.json();
        setDocuments(Array.isArray(updated) ? updated : []);
      }
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

  return (
    <div>
      {/* Upload button — staff only */}
      {staff && (
        <div className="mb-4 flex justify-end">
          <button
            type="button"
            onClick={() => setUploadOpen(true)}
            className="rounded-md px-4 py-2 text-sm font-medium text-white"
            style={{ backgroundColor: "#1B2B4B" }}
          >
            Upload Document
          </button>
        </div>
      )}

      {/* Document list */}
      {documents.length === 0 ? (
        <p className="py-6 text-center text-sm text-[#64748B]">No documents uploaded.</p>
      ) : (
        <ul className="divide-y divide-[#E2E8F0]">
          {documents.map((d) => (
            <li key={d.id} className="flex items-center justify-between py-3">
              <div>
                <p className="text-sm font-medium text-[#0F172A]">{d.file_name}</p>
                <p className="text-xs text-[#64748B]">
                  {d.document_type} · {formatDate(d.created_at)}
                </p>
              </div>
              <span className="rounded-full px-2 py-0.5 text-[10px] font-medium capitalize"
                style={{ background: "#F5F1EB", color: "#64748B" }}>
                {d.status}
              </span>
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
                <label className="mb-1 block text-xs font-medium text-[#334155]">
                  Document Type
                </label>
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
                <label className="mb-1 block text-xs font-medium text-[#334155]">
                  File *
                </label>
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
    </div>
  );
}
