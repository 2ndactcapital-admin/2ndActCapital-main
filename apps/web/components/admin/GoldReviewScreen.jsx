"use client";

/**
 * GoldReviewScreen — the note gold set (noteextractb1).
 *
 * Left: the sampler's proposed notes. Right: the chosen note's trimmed filing
 * text beside every reading any source produced for each field (rules,
 * EdgarTools, Model 1, Model 2, Jev, escalation, a person), with the current
 * gold value. A reviewer confirms a reading, corrects it, or marks the field
 * absent; FastAPI records the signed-in Super Admin as the reviewer.
 *
 * Field list, labels, vocabularies and the editable list come from the
 * response (Rule 1). Write controls render ONLY inside
 * `permissions.can_write === true` AND only for fields the envelope lists in
 * `vocabularies.editable` — no client-side default, no truthy fallback. A lost
 * envelope renders a read-only screen.
 */

import { useCallback, useMemo, useState, useTransition } from "react";

import { loadGoldNoteAction, saveGoldValueAction } from "@/lib/noteExtractionActions";
import { canWriteGold } from "@/lib/noteGoldGates.mjs";

const CARD = "rounded-md border border-[var(--2a-border)] bg-[var(--2a-bg-card)]";
const CONTROL =
  "rounded border border-[var(--2a-border)] bg-[var(--2a-bg-card)] px-2 py-1 text-xs text-[var(--2a-text)] focus:outline-none focus:ring-1 focus:ring-[var(--2a-gold)]";
const BUTTON =
  "rounded bg-[var(--2a-navy)] px-2.5 py-1 text-xs font-medium text-white disabled:opacity-50";
const GHOST =
  "rounded border border-[var(--2a-border)] px-2.5 py-1 text-xs text-[var(--2a-navy)] hover:bg-[var(--2a-bg)] disabled:opacity-50";
const EYEBROW =
  "block text-[10px] font-semibold uppercase tracking-[0.12em] text-[var(--2a-text-muted)]";
const ERROR_INK = "#9B2335";
const SUCCESS_INK = "#2D6A4F";

function show(value) {
  if (value === null || value === undefined) return "—";
  if (Array.isArray(value)) {
    return value
      .map((v) => (typeof v === "object" && v !== null
        ? `${v.name}${v.role ? ` (${v.role}${v.fee_pct != null ? `, ${v.fee_type ?? "fee"} ${v.fee_pct}%` : ""})` : ""}`
        : String(v)))
      .join("; ");
  }
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function parseInput(field, raw) {
  const text = (raw ?? "").trim();
  if (text === "") return { error: "enter a value, or mark the field absent" };
  switch (field.kind) {
    case "number": {
      const n = Number(text.replace(/[,%$]/g, ""));
      return Number.isFinite(n) ? { value: n } : { error: "not a number" };
    }
    case "boolean":
      if (text === "true" || text === "false") return { value: text === "true" };
      return { error: "true or false" };
    case "date":
      return /^\d{4}-\d{2}-\d{2}$/.test(text) ? { value: text } : { error: "use YYYY-MM-DD" };
    case "list_text":
      return { value: text.split(";").map((s) => s.trim()).filter(Boolean) };
    case "participants":
      try {
        const v = JSON.parse(text);
        return Array.isArray(v) ? { value: v } : { error: "a JSON list of participants" };
      } catch {
        return { error: "a JSON list of participants" };
      }
    default:
      return { value: text };
  }
}

function FieldRow({ field, readings, gold, writable, onSave, busy }) {
  const [draft, setDraft] = useState("");
  const [err, setErr] = useState(null);

  const submitCorrection = () => {
    const parsed = parseInput(field, draft);
    if (parsed.error) {
      setErr(parsed.error);
      return;
    }
    setErr(null);
    onSave(field.key, { action: "corrected", value: parsed.value });
  };

  return (
    <div className="border-b border-[var(--2a-border)] px-3 py-2">
      <div className="flex items-baseline justify-between gap-2">
        <div className="text-sm font-medium text-[var(--2a-navy)]">
          {field.label}
          {field.critical && (
            <span className="ml-2 text-[10px] uppercase tracking-wider" style={{ color: ERROR_INK }}>
              critical
            </span>
          )}
        </div>
        <div className="text-xs" style={{ color: gold ? SUCCESS_INK : "var(--2a-text-muted)" }}>
          {gold ? `gold: ${show(gold.value)} (${gold.action}, ${gold.reviewer_email ?? "reviewer"})` : "no gold value"}
        </div>
      </div>
      <ul className="mt-1 space-y-1">
        {readings.map((r) => (
          <li key={r.id} className="flex items-start justify-between gap-2 text-xs">
            <div className="min-w-0">
              <span className="font-semibold text-[var(--2a-text)]">{r.source}</span>
              {r.deployment_name && <span className="text-[var(--2a-text-muted)]"> · {r.deployment_name}</span>}
              {r.status !== "ok" && <span style={{ color: ERROR_INK }}> · {r.status}</span>}
              <span className="ml-1">{show(r.value)}</span>
              {r.probability != null && <span className="text-[var(--2a-text-muted)]"> · p={r.probability}</span>}
              {r.source_quote && (
                <div className="truncate text-[var(--2a-text-muted)]" title={r.source_quote}>
                  “{r.source_quote}” {r.quote_verified === false && <span style={{ color: ERROR_INK }}>(quote not found)</span>}
                </div>
              )}
            </div>
            {writable && r.value !== null && r.status === "ok" && (
              <button
                type="button"
                className={GHOST}
                disabled={busy}
                onClick={() => onSave(field.key, { action: "confirmed", value: r.value, source_reading_id: r.id })}
              >
                Confirm
              </button>
            )}
          </li>
        ))}
        {readings.length === 0 && <li className="text-xs text-[var(--2a-text-muted)]">No readings yet.</li>}
      </ul>
      {writable && (
        <div className="mt-1.5 flex items-center gap-2">
          {field.enum ? (
            <select className={CONTROL} value={draft} onChange={(e) => setDraft(e.target.value)}>
              <option value="">correct to…</option>
              {field.enum.map((v) => (
                <option key={v} value={v}>{v}</option>
              ))}
            </select>
          ) : (
            <input
              className={`${CONTROL} w-64`}
              placeholder={field.kind === "list_text" ? "name; name" : field.kind === "date" ? "YYYY-MM-DD" : "correct value"}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
            />
          )}
          <button type="button" className={BUTTON} disabled={busy} onClick={submitCorrection}>
            Correct
          </button>
          <button
            type="button"
            className={GHOST}
            disabled={busy}
            onClick={() => onSave(field.key, { action: "absent", value: null })}
          >
            Absent
          </button>
          {err && <span className="text-xs" style={{ color: ERROR_INK }}>{err}</span>}
        </div>
      )}
    </div>
  );
}

export default function GoldReviewScreen({ initial }) {
  const candidates = Array.isArray(initial?.rows) ? initial.rows : [];
  const [selected, setSelected] = useState(null);
  const [note, setNote] = useState(null);
  const [message, setMessage] = useState(null);
  const [pending, startTransition] = useTransition();

  const open = useCallback((filingId) => {
    setSelected(filingId);
    setMessage(null);
    startTransition(async () => {
      const res = await loadGoldNoteAction(filingId);
      if (res.ok) setNote(res.payload);
      else {
        setNote(null);
        setMessage(res.status === 403 ? "Super Admin access required." : res.error);
      }
    });
  }, []);

  const writable = canWriteGold(note);

  const byField = useMemo(() => {
    const m = {};
    for (const r of note?.readings ?? []) (m[r.field_key] ||= []).push(r);
    return m;
  }, [note]);
  const goldByField = useMemo(() => {
    const m = {};
    for (const g of note?.gold ?? []) m[g.field_key] = g;
    return m;
  }, [note]);

  const save = (fieldKey, body) => {
    if (!writable || !selected) return;
    startTransition(async () => {
      const res = await saveGoldValueAction(selected, fieldKey, body);
      if (!res.ok) {
        setMessage(res.status === 403 ? "Super Admin access required." : res.error);
        return;
      }
      const reload = await loadGoldNoteAction(selected);
      if (reload.ok) setNote(reload.payload);
      setMessage(`Saved ${fieldKey}.`);
    });
  };

  return (
    <div className="mt-6 grid grid-cols-12 gap-4">
      <div className={`${CARD} col-span-3 max-h-[80vh] overflow-y-auto`}>
        <div className="border-b border-[var(--2a-border)] px-3 py-2">
          <span className={EYEBROW}>Proposed notes ({candidates.length})</span>
        </div>
        {candidates.length === 0 && (
          <p className="p-3 text-xs text-[var(--2a-text-muted)]">
            No proposals yet. Run scripts/sample_gold_set.py --write.
          </p>
        )}
        {candidates.map((c) => (
          <button
            type="button"
            key={c.reference_filing_id}
            onClick={() => open(c.reference_filing_id)}
            className={`block w-full border-b border-[var(--2a-border)] px-3 py-2 text-left text-xs ${
              selected === c.reference_filing_id ? "bg-[var(--2a-bg)]" : ""
            }`}
          >
            <div className="font-medium text-[var(--2a-navy)]">{c.filer_name}</div>
            <div className="text-[var(--2a-text-muted)]">
              {c.form_type} · {c.filing_date} · {c.product_type} · {c.gold_fields} gold
            </div>
            {c.trap_tags?.length > 0 && (
              <div className="text-[10px] text-[var(--2a-text-muted)]">{c.trap_tags.join(", ")}</div>
            )}
          </button>
        ))}
      </div>

      <div className={`${CARD} col-span-4 max-h-[80vh] overflow-y-auto p-3`}>
        <span className={EYEBROW}>Trimmed filing text</span>
        {note?.tokens && (
          <div className="text-[10px] text-[var(--2a-text-muted)]">
            ~{note.tokens.trimmed} of ~{note.tokens.full} tokens kept
          </div>
        )}
        {note?.text_error && <p className="text-xs" style={{ color: ERROR_INK }}>{note.text_error}</p>}
        <pre className="mt-2 whitespace-pre-wrap font-sans text-xs leading-relaxed text-[var(--2a-text)]">
          {note?.trimmed_text ?? (pending ? "Loading…" : "Pick a note.")}
        </pre>
      </div>

      <div className={`${CARD} col-span-5 max-h-[80vh] overflow-y-auto`}>
        <div className="flex items-center justify-between border-b border-[var(--2a-border)] px-3 py-2">
          <span className={EYEBROW}>
            {note ? `${note.filing.filer_name} · ${note.filing.accession_number}` : "Fields"}
          </span>
          {message && <span className="text-xs text-[var(--2a-text-muted)]">{message}</span>}
        </div>
        {note && !writable && (
          <p className="px-3 py-2 text-xs text-[var(--2a-text-muted)]">Read-only.</p>
        )}
        {(note?.fields ?? []).map((f) => (
          <FieldRow
            key={f.key}
            field={f}
            readings={byField[f.key] ?? []}
            gold={goldByField[f.key]}
            writable={writable}
            onSave={save}
            busy={pending}
          />
        ))}
      </div>
    </div>
  );
}
