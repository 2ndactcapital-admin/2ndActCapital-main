"use client";

/**
 * GoldReviewScreen — the note gold set (noteextractb1, rebuilt for goldset on
 * the v3 registry).
 *
 * One note at a time. Left: the filing's document text, with the selected
 * field's quote highlighted. Right: the fields grouped by the server's
 * sections — critical, economics and distribution first and expanded, the
 * rest collapsed. Each field shows the pre-filled value, its quote, and
 * whether it came from the rules, the model or a derivation. Per field:
 * Confirm, Correct (a range edits min / max / bound; a list edits rows) or
 * Absent. Keyboard: J / K next / previous field, C confirm, A absent, E edit,
 * N next note. There is no "confirm all": every field is its own decision.
 *
 * Field list, sections, member fields, vocabularies and the editable list come
 * from the response (Rule 1). Write controls render ONLY when
 * canWriteGold(payload) — `permissions.can_write === true` and "value" in
 * `vocabularies.editable` — with no client-side default and no truthy
 * fallback. A lost envelope renders a read-only screen.
 */

import { useCallback, useEffect, useMemo, useRef, useState, useTransition } from "react";

import {
  loadGoldCandidatesAction,
  loadGoldNoteAction,
  saveGoldValueAction,
  skipGoldNoteAction,
} from "@/lib/noteExtractionActions";
import { canWriteGold } from "@/lib/noteGoldGates.mjs";
import {
  highlightSegments,
  keyAction,
  listRows,
  orderedFields,
  parseList,
  parseRange,
  parseScalar,
  showValue,
  step,
} from "@/lib/noteGoldReview.mjs";

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
const HIGHLIGHT = "#E8D5A3";

function sectionTitle(key) {
  return String(key ?? "").replace(/_/g, " ");
}

function Editor({ field, start, busy, onSubmit, onCancel }) {
  const [text, setText] = useState(() =>
    start == null || typeof start === "object" ? "" : String(start),
  );
  const [min, setMin] = useState(start?.min ?? "");
  const [max, setMax] = useState(start?.max ?? "");
  const [bound, setBound] = useState(start?.bound ?? "");
  const [rows, setRows] = useState(() => {
    const r = listRows(field, start);
    return r.length ? r : [Object.fromEntries((field.members ?? []).map((m) => [m.key, ""]))];
  });
  const [err, setErr] = useState(null);

  const submit = () => {
    const parsed =
      field.kind === "range"
        ? parseRange(field, min, max, bound)
        : field.kind === "list"
          ? parseList(field, rows)
          : parseScalar(field, text);
    if (parsed.error) {
      setErr(parsed.error);
      return;
    }
    setErr(null);
    onSubmit(parsed.value);
  };

  return (
    <div className="mt-2 rounded border border-[var(--2a-border)] bg-[var(--2a-bg)] p-2">
      {field.kind === "range" && (
        <div className="flex flex-wrap items-center gap-2">
          <label className="text-xs text-[var(--2a-text-muted)]">
            min <input autoFocus className={`${CONTROL} w-28`} value={min} onChange={(e) => setMin(e.target.value)} />
          </label>
          <label className="text-xs text-[var(--2a-text-muted)]">
            max <input className={`${CONTROL} w-28`} value={max} onChange={(e) => setMax(e.target.value)} />
          </label>
          <select className={CONTROL} value={bound} onChange={(e) => setBound(e.target.value)}>
            <option value="">bound (from min / max)</option>
            {(field.range_bounds ?? []).map((b) => (
              <option key={b} value={b}>{b}</option>
            ))}
          </select>
        </div>
      )}
      {field.kind === "list" && (
        <div className="overflow-x-auto">
          <table className="text-xs">
            <thead>
              <tr>
                {(field.members ?? []).map((m) => (
                  <th key={m.key} className="px-1 text-left font-medium text-[var(--2a-text-muted)]">
                    {m.key}{m.required ? " *" : ""}
                  </th>
                ))}
                <th />
              </tr>
            </thead>
            <tbody>
              {rows.map((row, i) => (
                <tr key={i}>
                  {(field.members ?? []).map((m) => (
                    <td key={m.key} className="px-1 py-0.5">
                      {m.kind === "enum" ? (
                        <select
                          className={CONTROL}
                          value={row[m.key] ?? ""}
                          onChange={(e) => setRows(rows.map((r, j) => (j === i ? { ...r, [m.key]: e.target.value } : r)))}
                        >
                          <option value="">—</option>
                          {(m.enum ?? []).map((v) => (
                            <option key={v} value={v}>{v}</option>
                          ))}
                        </select>
                      ) : (
                        <input
                          className={`${CONTROL} w-28`}
                          placeholder={m.kind === "date" ? "YYYY-MM-DD" : ""}
                          value={row[m.key] ?? ""}
                          onChange={(e) => setRows(rows.map((r, j) => (j === i ? { ...r, [m.key]: e.target.value } : r)))}
                        />
                      )}
                    </td>
                  ))}
                  <td>
                    <button type="button" className={GHOST} onClick={() => setRows(rows.filter((_r, j) => j !== i))}>
                      Remove
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <button
            type="button"
            className={`${GHOST} mt-1`}
            onClick={() => setRows([...rows, Object.fromEntries((field.members ?? []).map((m) => [m.key, ""]))])}
          >
            Add row
          </button>
        </div>
      )}
      {field.kind !== "range" && field.kind !== "list" && (
        field.enum || field.kind === "boolean" ? (
          <select autoFocus className={CONTROL} value={text} onChange={(e) => setText(e.target.value)}>
            <option value="">choose…</option>
            {(field.kind === "boolean" ? ["true", "false"] : field.enum).map((v) => (
              <option key={v} value={v}>{v}</option>
            ))}
          </select>
        ) : (
          <input
            autoFocus
            className={`${CONTROL} w-72`}
            placeholder={field.kind === "date" ? "YYYY-MM-DD" : "correct value"}
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") submit();
              if (e.key === "Escape") onCancel();
            }}
          />
        )
      )}
      <div className="mt-2 flex items-center gap-2">
        <button type="button" className={BUTTON} disabled={busy} onClick={submit}>Save correction</button>
        <button type="button" className={GHOST} onClick={onCancel}>Cancel</button>
        {err && <span className="text-xs" style={{ color: ERROR_INK }}>{err}</span>}
      </div>
    </div>
  );
}

function FieldRow({ field, prefill, gold, selected, editing, writable, busy, onSelect, onConfirm, onAbsent,
                    onEdit, onCorrect, onCancel, rowRef }) {
  return (
    <div
      ref={rowRef}
      onClick={onSelect}
      className={`cursor-pointer border-b border-[var(--2a-border)] px-3 py-2 ${selected ? "bg-[var(--2a-bg)]" : ""}`}
      style={selected ? { boxShadow: `inset 3px 0 0 ${HIGHLIGHT}` } : undefined}
    >
      <div className="flex items-baseline justify-between gap-2">
        <div className="text-sm font-medium text-[var(--2a-navy)]">
          {field.label}
          {field.critical && (
            <span className="ml-2 text-[10px] uppercase tracking-wider" style={{ color: ERROR_INK }}>critical</span>
          )}
          <span className="ml-2 text-[10px] text-[var(--2a-text-muted)]">{field.kind}</span>
        </div>
        <div className="text-xs" style={{ color: gold ? SUCCESS_INK : "var(--2a-text-muted)" }}>
          {gold ? `${gold.action}: ${showValue(gold.value)}` : "not reviewed"}
        </div>
      </div>
      <div className="mt-1 text-xs">
        <span className="text-[var(--2a-text-muted)]">pre-fill </span>
        {prefill ? (
          <>
            <span className="font-medium text-[var(--2a-text)]">{showValue(prefill.value)}</span>
            <span className="ml-1 rounded border border-[var(--2a-border)] px-1 text-[10px] text-[var(--2a-text-muted)]">
              {prefill.came_from}
            </span>
            {prefill.quote && (
              <div className="mt-0.5 truncate text-[var(--2a-text-muted)]" title={prefill.quote}>
                “{prefill.quote}”
                {prefill.quote_verified === false && <span style={{ color: ERROR_INK }}> (quote not found)</span>}
              </div>
            )}
          </>
        ) : (
          <span className="text-[var(--2a-text-muted)]">none</span>
        )}
      </div>
      {writable && selected && !editing && (
        <div className="mt-1.5 flex items-center gap-2">
          <button
            type="button"
            className={GHOST}
            disabled={busy || !prefill || prefill.value === null}
            onClick={(e) => { e.stopPropagation(); onConfirm(); }}
            title="C"
          >
            Confirm
          </button>
          <button type="button" className={GHOST} disabled={busy} onClick={(e) => { e.stopPropagation(); onEdit(); }} title="E">
            Correct
          </button>
          <button type="button" className={GHOST} disabled={busy} onClick={(e) => { e.stopPropagation(); onAbsent(); }} title="A">
            Absent
          </button>
        </div>
      )}
      {writable && editing && (
        <div onClick={(e) => e.stopPropagation()}>
          <Editor
            field={field}
            start={gold?.value ?? prefill?.value ?? null}
            busy={busy}
            onSubmit={onCorrect}
            onCancel={onCancel}
          />
        </div>
      )}
    </div>
  );
}

export default function GoldReviewScreen({ initial }) {
  const [list, setList] = useState(initial);
  const candidates = useMemo(() => (Array.isArray(list?.rows) ? list.rows : []), [list]);
  const batches = Array.isArray(list?.batches) ? list.batches : [];
  const [selectedNote, setSelectedNote] = useState(null);
  const [note, setNote] = useState(null);
  const [fieldIdx, setFieldIdx] = useState(0);
  const [editing, setEditing] = useState(false);
  const [collapsed, setCollapsed] = useState({});
  const [skipReason, setSkipReason] = useState("");
  const [message, setMessage] = useState(null);
  const [pending, startTransition] = useTransition();
  const markRef = useRef(null);
  const rowRefs = useRef({});

  const writable = canWriteGold(note);
  const fields = useMemo(() => orderedFields(note), [note]);
  const field = fields[fieldIdx] ?? null;
  const prefillBy = useMemo(() => note?.prefill?.by_field ?? {}, [note]);
  const goldBy = useMemo(() => {
    const m = {};
    for (const g of note?.gold ?? []) m[g.field_key] = g;
    return m;
  }, [note]);

  const open = useCallback((filingId) => {
    setSelectedNote(filingId);
    setMessage(null);
    setEditing(false);
    startTransition(async () => {
      const res = await loadGoldNoteAction(filingId);
      if (res.ok) {
        setNote(res.payload);
        setFieldIdx(0);
        const c = {};
        for (const s of res.payload?.sections ?? []) c[s.key] = !s.expanded;
        setCollapsed(c);
      } else {
        setNote(null);
        setMessage(res.status === 403 ? "Super Admin access required." : res.error);
      }
    });
  }, []);

  const reloadList = (batch) => {
    startTransition(async () => {
      const res = await loadGoldCandidatesAction(batch ? { batch } : {});
      if (res.ok) setList(res.payload);
      else setMessage(res.status === 403 ? "Super Admin access required." : res.error);
    });
  };

  const nextNote = useCallback(() => {
    const open_ = candidates.filter((c) => c.status !== "done" && c.status !== "skipped");
    const pool = open_.length ? open_ : candidates;
    if (!pool.length) return;
    const i = pool.findIndex((c) => c.reference_filing_id === selectedNote);
    open(pool[(i + 1) % pool.length].reference_filing_id);
  }, [candidates, selectedNote, open]);

  const save = useCallback(
    (fieldKey, body, advance = true) => {
      if (!writable || !selectedNote) return;
      startTransition(async () => {
        const res = await saveGoldValueAction(selectedNote, fieldKey, body);
        if (!res.ok) {
          setMessage(res.status === 403 ? "Super Admin access required." : res.error);
          return;
        }
        const reload = await loadGoldNoteAction(selectedNote);
        if (reload.ok) setNote(reload.payload);
        setEditing(false);
        setMessage(`Saved ${fieldKey} (${body.action}).`);
        if (advance) setFieldIdx((i) => step(i, fields.length, "next"));
      });
    },
    [writable, selectedNote, fields.length],
  );

  const confirm = useCallback(() => {
    const p = field && prefillBy[field.key];
    if (!p || p.value === null) {
      setMessage("Nothing pre-filled to confirm — correct it or mark it absent.");
      return;
    }
    save(field.key, { action: "confirmed", value: p.value, source_reading_id: p.reading_id });
  }, [field, prefillBy, save]);

  const absent = useCallback(() => field && save(field.key, { action: "absent", value: null }), [field, save]);

  useEffect(() => {
    const onKey = (e) => {
      const a = keyAction(e);
      if (!a || !note) return;
      e.preventDefault();
      if (a === "next" || a === "prev") {
        setEditing(false);
        setFieldIdx((i) => step(i, fields.length, a));
      } else if (a === "nextNote") nextNote();
      else if (!writable) return;
      else if (a === "confirm") confirm();
      else if (a === "absent") absent();
      else if (a === "edit") setEditing(true);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [note, fields.length, writable, confirm, absent, nextNote]);

  // keep the selected field and its quote in view (its section is always shown open)
  useEffect(() => {
    if (!field) return;
    rowRefs.current[field.key]?.scrollIntoView({ block: "nearest" });
    markRef.current?.scrollIntoView({ block: "center" });
  }, [field, note]);

  const span = useMemo(() => {
    if (!field) return null;
    const g = goldBy[field.key];
    if (g && Number.isInteger(g.text_start)) return [g.text_start, g.text_end];
    const p = prefillBy[field.key];
    if (p && Number.isInteger(p.text_start)) return [p.text_start, p.text_end];
    return null;
  }, [field, goldBy, prefillBy]);
  const segments = useMemo(
    () => highlightSegments(note?.text ?? "", span?.[0], span?.[1]),
    [note, span],
  );

  const skip = () => {
    if (!writable || !selectedNote) return;
    if (skipReason.trim().length < 3) {
      setMessage("Give a reason to skip this note.");
      return;
    }
    startTransition(async () => {
      const res = await skipGoldNoteAction(selectedNote, skipReason.trim());
      if (!res.ok) {
        setMessage(res.status === 403 ? "Super Admin access required." : res.error);
        return;
      }
      setSkipReason("");
      setMessage("Skipped.");
      reloadList(list?.batch);
    });
  };

  const progress = list?.progress;
  const noteProgress = note?.progress;

  return (
    <div className="mt-6 space-y-4">
      <div className={`${CARD} flex flex-wrap items-center gap-3 px-3 py-2`}>
        <label className="text-xs text-[var(--2a-text-muted)]">
          Batch{" "}
          <select className={CONTROL} value={list?.batch ?? ""} onChange={(e) => reloadList(e.target.value || null)}>
            <option value="">all batches</option>
            {batches.map((b) => (
              <option key={b} value={b}>{b}</option>
            ))}
          </select>
        </label>
        <label className="text-xs text-[var(--2a-text-muted)]">
          Note{" "}
          <select className={`${CONTROL} max-w-md`} value={selectedNote ?? ""} onChange={(e) => e.target.value && open(e.target.value)}>
            <option value="">pick a note…</option>
            {candidates.map((c) => (
              <option key={c.reference_filing_id} value={c.reference_filing_id}>
                {c.issuer_group ?? c.filer_name} · {c.filing_year} · {c.product_type} · {c.status}
              </option>
            ))}
          </select>
        </label>
        <button type="button" className={GHOST} onClick={nextNote} disabled={!candidates.length} title="N">
          Next note
        </button>
        {progress && (
          <span className="text-xs text-[var(--2a-text-muted)]">
            notes done {progress.notes_done} / {progress.notes_total}
          </span>
        )}
        {noteProgress && (
          <span className="text-xs text-[var(--2a-text-muted)]">
            fields done {noteProgress.fields_done} / {noteProgress.fields_total}
          </span>
        )}
        {message && <span className="text-xs text-[var(--2a-navy)]">{message}</span>}
      </div>

      {(note?.anchoring_note || list?.anchoring_note) && (
        <p className="text-xs text-[var(--2a-text-muted)]">
          <span className="font-semibold">Anchoring:</span> {note?.anchoring_note ?? list.anchoring_note}
        </p>
      )}

      {candidates.length === 0 && (
        <div className={`${CARD} p-6 text-center text-sm text-[var(--2a-text-muted)]`}>
          No proposals yet. Run scripts/build_gold_set.py --propose.
        </div>
      )}

      {note && (
        <div className="grid grid-cols-12 gap-4">
          <div className={`${CARD} col-span-6 max-h-[78vh] overflow-y-auto p-3`}>
            <span className={EYEBROW}>
              {note.filing.filer_name} · {note.filing.accession_number} · {note.filing.filing_date}
            </span>
            {note.candidate?.trap_tags?.length > 0 && (
              <div className="text-[10px] text-[var(--2a-text-muted)]">traps: {note.candidate.trap_tags.join(", ")}</div>
            )}
            {note.text_error && <p className="text-xs" style={{ color: ERROR_INK }}>{note.text_error}</p>}
            <pre className="mt-2 whitespace-pre-wrap font-sans text-xs leading-relaxed text-[var(--2a-text)]">
              {segments.map((s, i) =>
                s.mark ? (
                  <mark key={i} ref={markRef} style={{ background: HIGHLIGHT }}>{s.text}</mark>
                ) : (
                  <span key={i}>{s.text}</span>
                ),
              )}
            </pre>
          </div>

          <div className={`${CARD} col-span-6 max-h-[78vh] overflow-y-auto`}>
            {!writable && <p className="px-3 py-2 text-xs text-[var(--2a-text-muted)]">Read-only.</p>}
            {writable && (
              <div className="flex items-center gap-2 border-b border-[var(--2a-border)] px-3 py-2">
                <input
                  className={`${CONTROL} flex-1`}
                  placeholder="reason to skip this note"
                  value={skipReason}
                  onChange={(e) => setSkipReason(e.target.value)}
                />
                <button type="button" className={GHOST} disabled={pending} onClick={skip}>Skip note</button>
              </div>
            )}
            {(note.sections ?? []).map((s) => (
              <div key={s.key}>
                <button
                  type="button"
                  className="flex w-full items-center justify-between border-b border-[var(--2a-border)] bg-[var(--2a-bg)] px-3 py-1.5 text-left"
                  onClick={() => setCollapsed((c) => ({ ...c, [s.key]: !c[s.key] }))}
                >
                  <span className={EYEBROW}>{sectionTitle(s.key)}</span>
                  <span className="text-[10px] text-[var(--2a-text-muted)]">
                    {(s.fields ?? []).filter((k) => goldBy[k]).length} / {(s.fields ?? []).length}
                    {collapsed[s.key] ? " ▸" : " ▾"}
                  </span>
                </button>
                {(!collapsed[s.key] || (field && (s.fields ?? []).includes(field.key))) &&
                  (s.fields ?? []).map((k) => {
                    const f = fields.find((x) => x.key === k);
                    if (!f) return null;
                    const idx = fields.indexOf(f);
                    return (
                      <FieldRow
                        key={k}
                        rowRef={(el) => { rowRefs.current[k] = el; }}
                        field={f}
                        prefill={prefillBy[k]}
                        gold={goldBy[k]}
                        selected={idx === fieldIdx}
                        editing={idx === fieldIdx && editing}
                        writable={writable}
                        busy={pending}
                        onSelect={() => { setFieldIdx(idx); setEditing(false); }}
                        onConfirm={confirm}
                        onAbsent={absent}
                        onEdit={() => setEditing(true)}
                        onCorrect={(value) => save(k, { action: "corrected", value })}
                        onCancel={() => setEditing(false)}
                      />
                    );
                  })}
              </div>
            ))}
          </div>
        </div>
      )}
      <p className="text-[11px] text-[var(--2a-text-muted)]">
        Keys: J / K next / previous field · C confirm · A absent · E edit · N next note. Each field is its own decision.
      </p>
    </div>
  );
}
