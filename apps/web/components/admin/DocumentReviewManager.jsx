"use client";

import { useState, useTransition } from "react";
import EntityPicker from "@/components/EntityPicker";
import {
  confirmDocumentAction,
  linkEntityAction,
  refreshReviewAction,
  submitFieldCorrectionAction,
  unlinkEntityAction,
} from "@/lib/documentReviewActions";

const CARD = { borderColor: "#ece8dd", boxShadow: "0 1px 3px rgba(0,0,0,0.06)" };

function Card({ title, hint, right, children }) {
  return (
    <section className="rounded-lg border bg-bg-card p-5" style={CARD}>
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-base font-semibold text-navy">{title}</h2>
          {hint && <p className="mt-1 text-sm text-text-muted">{hint}</p>}
        </div>
        {right}
      </div>
      <div className="mt-4">{children}</div>
    </section>
  );
}

function inputClass() {
  return "rounded-md border border-border bg-bg-card px-3 py-2 text-sm text-text-primary outline-none focus:ring-2 focus:ring-navy";
}

function Pill({ children }) {
  return (
    <span className="inline-flex items-center rounded-full bg-gold-light px-2 py-0.5 text-[11px] font-medium text-navy">
      {children}
    </span>
  );
}

// Humanise a mapped_fields key ("ordinary_business_income" → "Ordinary Business Income")
function humanize(key) {
  return String(key || "")
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

function FieldRow({ documentId, field, disabled, onSaved }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(field.value ?? "");
  const [notes, setNotes] = useState("");
  const [error, setError] = useState(null);
  const [pending, startTransition] = useTransition();

  function save() {
    setError(null);
    startTransition(async () => {
      const res = await submitFieldCorrectionAction(
        documentId, field.field_name, draft, notes,
      );
      if (res.ok) {
        setEditing(false);
        setNotes("");
        onSaved();
      } else {
        setError(res.error || "Could not save correction.");
      }
    });
  }

  return (
    <div className="border-t border-border py-3 first:border-t-0">
      <div className="flex items-center justify-between gap-4">
        <div className="min-w-0">
          <div className="text-xs font-medium uppercase tracking-wide text-text-muted">
            {humanize(field.field_name)}
          </div>
          {editing ? (
            <div className="mt-2 space-y-2">
              <input
                className={inputClass() + " w-full"}
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                disabled={pending}
                aria-label={`Correct ${humanize(field.field_name)}`}
              />
              <input
                className={inputClass() + " w-full"}
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
                placeholder="Reason for correction (optional)"
                disabled={pending}
              />
              {error && <p className="text-sm text-[#9B2335]">{error}</p>}
            </div>
          ) : (
            <div className="mt-1 text-sm text-text-primary break-words">
              {field.value === "" || field.value == null ? (
                <span className="text-text-muted">—</span>
              ) : (
                field.value
              )}
            </div>
          )}
        </div>

        <div className="flex shrink-0 items-center gap-3">
          {/* Confidence — genuinely NOT available from this extraction path. */}
          <span className="text-xs text-text-muted" title="No confidence score is produced by K-1 extraction">
            Confidence: n/a
          </span>
          {editing ? (
            <>
              <button
                onClick={save}
                disabled={pending}
                className="text-sm font-medium text-navy hover:underline disabled:opacity-60"
              >
                {pending ? "Saving…" : "Save"}
              </button>
              <button
                onClick={() => { setEditing(false); setDraft(field.value ?? ""); setError(null); }}
                disabled={pending}
                className="text-sm font-medium text-text-muted hover:underline"
              >
                Cancel
              </button>
            </>
          ) : (
            <button
              onClick={() => { setDraft(field.value ?? ""); setEditing(true); }}
              disabled={disabled}
              className="text-sm font-medium text-navy hover:underline disabled:opacity-40"
            >
              Edit
            </button>
          )}
        </div>
      </div>

      {/* Source location — honest page reference, never a fabricated highlight. */}
      <div className="mt-1 text-[11px] text-text-muted">
        Source: {field.source_location?.page_count
          ? `page reference (document has ${field.source_location.page_count} page(s))`
          : "see attached document"} — no on-page coordinates captured
      </div>
    </div>
  );
}

export default function DocumentReviewManager({ documentId, initialPayload }) {
  const [payload, setPayload] = useState(initialPayload);
  const [banner, setBanner] = useState(null);
  const [pickerEntity, setPickerEntity] = useState(null);
  const [pending, startTransition] = useTransition();

  const doc = payload?.document || {};
  const isConfirmed = doc.status === "confirmed" || !!doc.confirmed_at;
  const fields = payload?.fields || [];
  const links = payload?.links || { entity_links: [], record_links: [] };
  const coordsAvailable = payload?.source_location?.coordinates_available;

  function refresh() {
    startTransition(async () => {
      const res = await refreshReviewAction(documentId);
      if (res.ok) setPayload(res.payload);
      else setBanner({ type: "error", text: res.error || "Could not refresh." });
    });
  }

  async function handleDownload() {
    setBanner(null);
    const res = await fetch(`/api/documents/${documentId}/download`);
    const data = await res.json().catch(() => ({}));
    if (res.ok && data.url) {
      window.open(data.url, "_blank", "noopener");
    } else {
      setBanner({ type: "error", text: data.detail || data.error || "No stored file to open." });
    }
  }

  function confirm() {
    setBanner(null);
    startTransition(async () => {
      const res = await confirmDocumentAction(documentId);
      if (res.ok) {
        setBanner({ type: "ok", text: "Document confirmed." });
        refresh();
      } else {
        setBanner({ type: "error", text: res.error || "Could not confirm." });
      }
    });
  }

  function addEntityLink() {
    if (!pickerEntity?.id) return;
    setBanner(null);
    startTransition(async () => {
      const res = await linkEntityAction(documentId, pickerEntity.id, "manual");
      if (res.ok) {
        setPickerEntity(null);
        refresh();
      } else {
        setBanner({ type: "error", text: res.error || "Could not link entity." });
      }
    });
  }

  function removeEntityLink(entityId) {
    setBanner(null);
    startTransition(async () => {
      const res = await unlinkEntityAction(documentId, entityId);
      if (res.ok) refresh();
      else setBanner({ type: "error", text: res.error || "Could not unlink." });
    });
  }

  return (
    <div className="mt-6 space-y-6">
      {banner && (
        <div
          className={
            "rounded-md border border-border bg-bg-card px-4 py-2 text-sm " +
            (banner.type === "error" ? "text-[#9B2335]" : "text-[#2D6A4F]")
          }
        >
          {banner.text}
        </div>
      )}

      <Card
        title={doc.original_filename || "Document"}
        hint={`Status: ${doc.status || "—"}${doc.doc_family ? ` · ${doc.doc_family}` : ""}`}
        right={
          <div className="flex items-center gap-3">
            {isConfirmed && <Pill>Confirmed</Pill>}
            {doc.has_stored_file && (
              <button
                onClick={handleDownload}
                className="rounded-md border border-border px-3 py-2 text-sm font-medium text-navy hover:bg-bg-app"
              >
                See attached document
              </button>
            )}
          </div>
        }
      >
        <div className="text-sm text-text-secondary">
          {payload?.template_extraction ? (
            <>
              Template: <span className="font-medium">{payload.template_extraction.template_type}</span>
              {" · "}source: <span className="font-medium">{payload.template_extraction.extraction_source}</span>
            </>
          ) : (
            <span className="text-text-muted">No template extraction for this document.</span>
          )}
        </div>
        {/* Honest source-location statement for the whole document. */}
        <div className="mt-3 rounded-md border border-border bg-bg-app px-3 py-2 text-xs text-text-muted">
          {coordsAvailable
            ? "On-page source coordinates are available."
            : "On-page source coordinates were not captured during extraction — fields reference the attached document by page rather than a precise highlight."}
        </div>
      </Card>

      <Card
        title="Extracted fields"
        hint="Confidence is not produced by this extraction; correct any value inline — the corrected value becomes the system of record."
      >
        {fields.length === 0 ? (
          <p className="text-sm text-text-muted">No mapped fields to review.</p>
        ) : (
          <div>
            {fields.map((f) => (
              <FieldRow
                key={f.field_name}
                documentId={documentId}
                field={f}
                disabled={pending}
                onSaved={refresh}
              />
            ))}
          </div>
        )}
      </Card>

      <Card
        title="Linked entities & records"
        hint="Reuses the document-linkage engine. Add or remove entity links; record links are shown for reference."
      >
        <div className="space-y-2">
          {links.entity_links.length === 0 && links.record_links.length === 0 && (
            <p className="text-sm text-text-muted">No links yet.</p>
          )}
          {links.entity_links.map((l) => (
            <div key={l.link_id} className="flex items-center justify-between gap-3">
              <div className="text-sm text-text-primary">
                {l.display_name}
                {l.link_role && <span className="ml-2 text-xs text-text-muted">({l.link_role})</span>}
                {l.system_created && <span className="ml-2"><Pill>auto</Pill></span>}
              </div>
              <button
                onClick={() => removeEntityLink(l.entity_id)}
                disabled={pending}
                className="text-sm font-medium text-[#9B2335] hover:underline disabled:opacity-60"
              >
                Remove
              </button>
            </div>
          ))}
          {links.record_links.map((l) => (
            <div key={l.link_id} className="text-sm text-text-secondary">
              {l.record_type}: <span className="font-mono text-xs">{l.record_id}</span>
            </div>
          ))}
        </div>

        <div className="mt-4 flex items-end gap-3">
          <div className="flex-1">
            <div className="mb-1 text-xs font-medium uppercase tracking-wide text-text-muted">
              Add entity link
            </div>
            <EntityPicker value={pickerEntity} onChange={setPickerEntity} placeholder="Search entities…" />
          </div>
          <button
            onClick={addEntityLink}
            disabled={pending || !pickerEntity?.id}
            className="rounded-md bg-navy px-4 py-2 text-sm font-medium text-bg-app hover:opacity-90 disabled:opacity-60"
          >
            Link
          </button>
        </div>
      </Card>

      <Card
        title="Confirm review"
        hint={isConfirmed
          ? "This document has been confirmed."
          : "Mark this document reviewed once the fields and links are correct."}
        right={
          <button
            onClick={confirm}
            disabled={pending || isConfirmed}
            className="rounded-md bg-navy px-4 py-2 text-sm font-medium text-bg-app hover:opacity-90 disabled:opacity-60"
          >
            {isConfirmed ? "Confirmed" : pending ? "Working…" : "Confirm reviewed"}
          </button>
        }
      >
        {isConfirmed && doc.confirmed_at && (
          <p className="text-sm text-text-muted">Confirmed at {doc.confirmed_at}</p>
        )}
      </Card>
    </div>
  );
}
