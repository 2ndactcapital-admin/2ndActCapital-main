"use client";

// Chancery Phase 9 — the reusable contextual Documents panel.
//
// ONE component, embedded unchanged into every record page (entity / SPV / deal /
// transaction / …). It surfaces documents LINKED to a record via the Phase-5
// linkage tables (document_entity_links / document_record_links) — it is NOT the
// manual upload/versioning "Documents" tab (that is a separate subsystem), and it
// is NOT search (search is a separate, explicit action — see /admin/document-search).
//
// Props:
//   recordType — "entity" | "spv" | "deal" | "transaction" | … (drives the query)
//   recordId   — the record's uuid
//   title      — optional heading override
//
// Data comes from the collision-free Next route /api/records/[recordType]/[recordId]
// /documents (Rule 5: the browser never calls FastAPI directly). Clicking a row
// opens Phase-6's real review/confirm screen — reused, not duplicated.

import { useCallback, useEffect, useState } from "react";
import { formatDate } from "@/lib/format";

const CARD = { borderColor: "#ece8dd", boxShadow: "0 1px 3px rgba(0,0,0,0.06)" };

// Status styling reuses existing brand conventions only — success green
// (#E8F5E9/#2D6A4F) and the #EEF4FF/navy "in-progress" pill already used on the
// SPV page, plus brand tokens. No Signature-palette hex is hardcoded.
const STATUS_CFG = {
  confirmed: { label: "Confirmed", bg: "#E8F5E9", color: "#2D6A4F" },
  sorted: { label: "Sorted", bg: "#EEF4FF", color: "var(--2a-navy)" },
  extracted: { label: "Extracted", bg: "#EEF4FF", color: "var(--2a-navy)" },
  pending_review: { label: "Pending review", bg: "var(--2a-gold-light)", color: "var(--2a-navy)" },
  dropped: { label: "Dropped", bg: "var(--2a-bg-sidebar)", color: "var(--2a-text-muted)" },
};

function StatusPill({ status }) {
  const cfg = STATUS_CFG[status] || {
    label: status || "—",
    bg: "var(--2a-bg-sidebar)",
    color: "var(--2a-text-muted)",
  };
  return (
    <span
      className="inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium capitalize"
      style={{ backgroundColor: cfg.bg, color: cfg.color }}
    >
      {cfg.label}
    </span>
  );
}

function LinkedByPill({ systemCreated }) {
  return (
    <span
      className="inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium"
      style={
        systemCreated
          ? { backgroundColor: "var(--2a-gold-light)", color: "var(--2a-navy)" }
          : { backgroundColor: "var(--2a-bg-sidebar)", color: "var(--2a-text-muted)" }
      }
      title={systemCreated ? "Linked automatically by the system" : "Linked by a team member"}
    >
      {systemCreated ? "auto" : "manual"}
    </span>
  );
}

export default function DocumentsPanel({ recordType, recordId, title = "Documents" }) {
  const [docs, setDocs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    if (!recordType || !recordId) return;
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(
        `/api/records/${encodeURIComponent(recordType)}/${encodeURIComponent(recordId)}/documents`,
        { cache: "no-store" },
      );
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(data.error || "Could not load linked documents.");
        setDocs([]);
      } else {
        setDocs(Array.isArray(data.documents) ? data.documents : []);
      }
    } catch (err) {
      setError(err.message || "Could not load linked documents.");
      setDocs([]);
    } finally {
      setLoading(false);
    }
  }, [recordType, recordId]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <section className="rounded-lg border bg-bg-card p-5" style={CARD}>
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-base font-semibold text-navy">{title}</h2>
          <p className="mt-1 text-sm text-text-muted">
            Documents linked to this record. Click one to review or confirm it.
          </p>
        </div>
        <button
          type="button"
          onClick={load}
          disabled={loading}
          className="text-xs font-medium text-navy hover:underline disabled:opacity-50"
        >
          Refresh
        </button>
      </div>

      <div className="mt-4">
        {loading ? (
          <p className="py-6 text-center text-sm text-text-muted">Loading…</p>
        ) : error ? (
          <p className="py-6 text-center text-sm text-[#9B2335]">{error}</p>
        ) : docs.length === 0 ? (
          // Clean, unalarming empty state — a record with no links is normal.
          <div className="rounded-md border border-border bg-bg-app px-4 py-8 text-center">
            <p className="text-sm font-medium text-text-secondary">No linked documents yet</p>
            <p className="mt-1 text-xs text-text-muted">
              Documents linked to this record will appear here.
            </p>
          </div>
        ) : (
          <ul className="divide-y divide-border">
            {docs.map((d) => (
              <li key={d.link_id} className="py-3">
                <a
                  href={`/admin/document-review/${d.document_id}`}
                  className="group flex flex-wrap items-center justify-between gap-3"
                >
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium text-navy group-hover:underline">
                      {d.original_filename || "Untitled document"}
                    </p>
                    <div className="mt-1 flex flex-wrap items-center gap-1.5 text-xs text-text-muted">
                      {d.doc_family && <span className="capitalize">{d.doc_family}</span>}
                      {d.doc_family && d.document_created_at && <span>·</span>}
                      {d.document_created_at && (
                        <span>Uploaded {formatDate(d.document_created_at)}</span>
                      )}
                    </div>
                  </div>
                  <div className="flex shrink-0 flex-wrap items-center gap-2">
                    <StatusPill status={d.status} />
                    <LinkedByPill systemCreated={d.system_created} />
                  </div>
                </a>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}
