"use client";

import { useEffect, useRef, useState, useTransition } from "react";
import {
  loadNotesAction,
  addNoteAction,
  applyNoteUpdatesAction,
} from "@/lib/notesActions";

const NOTE_TYPES = [
  { value: "meeting", label: "Meeting" },
  { value: "call", label: "Call" },
  { value: "email", label: "Email" },
  { value: "other", label: "Other" },
];

function formatDate(iso) {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleDateString();
  } catch {
    return "";
  }
}

function NoteCard({ entityId, note, onApplied }) {
  const [expanded, setExpanded] = useState(false);
  const [applied, setApplied] = useState(false);
  const [pending, startTransition] = useTransition();
  const extracted = note.extracted_fields || {};
  const entityUpdates = extracted.entity_updates || {};
  const newAttributes = extracted.new_attributes || {};
  const hasUpdates =
    Object.keys(entityUpdates).length > 0 || Object.keys(newAttributes).length > 0;
  const long = (note.note_text || "").length > 220;
  const text = long && !expanded ? `${note.note_text.slice(0, 220)}…` : note.note_text;

  function apply() {
    startTransition(async () => {
      const res = await applyNoteUpdatesAction(entityId, note.id, {
        entity_updates: entityUpdates,
        new_attributes: newAttributes,
      });
      if (res.ok) {
        setApplied(true);
        onApplied?.();
      }
    });
  }

  return (
    <div className="rounded-lg border bg-bg-card p-4" style={{ borderColor: "#ece8dd" }}>
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className="rounded-full bg-gold-light px-2.5 py-0.5 text-xs font-medium text-navy">
            {note.note_type}
          </span>
          <span className="text-xs text-text-muted">
            {formatDate(note.meeting_date || note.created_at)}
          </span>
        </div>
        {note.extraction_status === "pending" && (
          <span className="text-xs text-text-muted">Processing…</span>
        )}
      </div>

      <p className="mt-2 whitespace-pre-line text-sm text-text-secondary">
        {text}{" "}
        {long && (
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            className="text-xs font-medium text-navy hover:underline"
          >
            {expanded ? "read less" : "read more"}
          </button>
        )}
      </p>

      {note.extraction_status === "completed" && hasUpdates && (
        <div className="mt-3 rounded-md border border-border bg-bg-app p-3">
          <p className="text-xs font-semibold uppercase tracking-wide text-text-muted">
            AI extracted
          </p>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {Object.entries(entityUpdates).map(([k, v]) => (
              <span
                key={`u-${k}`}
                className="rounded-full border border-border bg-bg-card px-2 py-0.5 text-xs text-text-secondary"
              >
                {k}: {String(v)}
              </span>
            ))}
            {Object.entries(newAttributes).map(([k, v]) => (
              <span
                key={`a-${k}`}
                className="rounded-full border border-border bg-bg-card px-2 py-0.5 text-xs text-text-secondary"
              >
                {k}: {String(v)}
              </span>
            ))}
          </div>
          {extracted.summary && (
            <p className="mt-2 text-xs text-text-muted">{extracted.summary}</p>
          )}
          <div className="mt-3 flex gap-2">
            {applied ? (
              <span className="text-xs font-medium text-[#2D6A4F]">Applied ✓</span>
            ) : (
              <>
                <button
                  type="button"
                  onClick={apply}
                  disabled={pending}
                  className="rounded-md bg-navy px-3 py-1.5 text-xs font-medium text-bg-app hover:opacity-90 disabled:opacity-60"
                >
                  Apply updates
                </button>
                <button
                  type="button"
                  onClick={() => setApplied(true)}
                  className="rounded-md border border-border px-3 py-1.5 text-xs font-medium text-text-secondary hover:bg-border"
                >
                  Dismiss
                </button>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

export default function NotesTab({ entityId, initial = [] }) {
  const [notes, setNotes] = useState(initial);
  const [loading, setLoading] = useState(initial.length === 0);
  const [showForm, setShowForm] = useState(false);
  const [noteType, setNoteType] = useState("meeting");
  const [meetingDate, setMeetingDate] = useState("");
  const [noteText, setNoteText] = useState("");
  const [pending, startTransition] = useTransition();
  const [error, setError] = useState(null);
  const pollRef = useRef(0);

  async function refresh() {
    const res = await loadNotesAction(entityId);
    if (res.ok) setNotes(res.notes || []);
    setLoading(false);
  }

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [entityId]);

  // Poll briefly while any note is still processing.
  useEffect(() => {
    const anyPending = notes.some((n) => n.extraction_status === "pending");
    if (!anyPending || pollRef.current > 5) return;
    const t = setTimeout(() => {
      pollRef.current += 1;
      refresh();
    }, 4000);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [notes]);

  function submit() {
    if (!noteText.trim()) return;
    setError(null);
    startTransition(async () => {
      const res = await addNoteAction(entityId, {
        note_text: noteText.trim(),
        note_type: noteType,
        meeting_date: meetingDate || null,
      });
      if (res.ok) {
        setNotes((prev) => [res.note, ...prev]);
        setNoteText("");
        setMeetingDate("");
        setShowForm(false);
        pollRef.current = 0;
      } else {
        setError(res.error);
      }
    });
  }

  return (
    <section>
      <div className="flex items-center justify-between">
        <h2 className="text-base font-semibold text-navy">Notes</h2>
        {!showForm && (
          <button
            type="button"
            onClick={() => setShowForm(true)}
            className="rounded-md bg-navy px-3 py-1.5 text-sm font-medium text-bg-app hover:opacity-90"
          >
            Add Note
          </button>
        )}
      </div>

      {showForm && (
        <div className="mt-4 rounded-lg border border-border bg-bg-card p-4">
          <div className="flex flex-wrap gap-3">
            <select
              value={noteType}
              onChange={(e) => setNoteType(e.target.value)}
              className="rounded-md border border-border bg-bg-card px-3 py-2 text-sm text-text-primary outline-none focus:ring-2 focus:ring-navy"
            >
              {NOTE_TYPES.map((t) => (
                <option key={t.value} value={t.value}>
                  {t.label}
                </option>
              ))}
            </select>
            <input
              type="date"
              value={meetingDate}
              onChange={(e) => setMeetingDate(e.target.value)}
              className="rounded-md border border-border bg-bg-card px-3 py-2 text-sm text-text-primary outline-none focus:ring-2 focus:ring-navy"
            />
          </div>
          <textarea
            value={noteText}
            onChange={(e) => setNoteText(e.target.value)}
            rows={5}
            placeholder="Type your meeting notes... AI will extract key updates."
            className="mt-3 w-full resize-y rounded-md border border-border bg-bg-card px-3 py-2 text-sm text-text-primary outline-none focus:ring-2 focus:ring-navy"
          />
          {error && <p className="mt-2 text-sm text-[#9B2335]">{error}</p>}
          <div className="mt-3 flex gap-2">
            <button
              type="button"
              onClick={submit}
              disabled={pending || !noteText.trim()}
              className="rounded-md bg-navy px-4 py-2 text-sm font-medium text-bg-app hover:opacity-90 disabled:opacity-60"
            >
              {pending ? "Saving…" : "Save"}
            </button>
            <button
              type="button"
              onClick={() => setShowForm(false)}
              className="rounded-md border border-border px-4 py-2 text-sm font-medium text-text-secondary hover:bg-border"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      <div className="mt-4 space-y-3">
        {loading ? (
          <p className="text-sm text-text-muted">Loading notes…</p>
        ) : notes.length === 0 ? (
          <div className="rounded-lg border border-border bg-bg-card p-10 text-center text-sm text-text-muted">
            No notes yet.
          </div>
        ) : (
          notes.map((n) => (
            <NoteCard key={n.id} entityId={entityId} note={n} onApplied={refresh} />
          ))
        )}
      </div>
    </section>
  );
}
