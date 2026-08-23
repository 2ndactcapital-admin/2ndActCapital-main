"use client";

/**
 * Note-terms review queue — list, per-row detail, and the STP policy panel.
 *
 * The screen exists because a trust policy with nothing to review against is
 * unverifiable. Its job is to show a reviewer the two answers the extraction
 * ensemble produced for a hazard field, side by side, with the sentence they
 * were both reading, and let them say which one is right.
 *
 * WHAT IT DOES NOT DO
 *   It never grants straight-through processing on its own. When the last
 *   outstanding row for an issuer/form pairing is settled it OFFERS the grant,
 *   with a notes field, and waits for a person to press the button. Trust is
 *   not inferred from a clean run.
 */

import { useMemo, useState, useTransition } from "react";
import DataGrid from "@/components/ui/DataGrid";
import {
  grantStpAction,
  refreshQueueAction,
  resolveFieldAction,
  revokeStpAction,
} from "@/lib/noteTermsQueueActions";

// Card hairline + shadow, matching every other admin surface.
const CARD = { borderColor: "var(--2a-border)", boxShadow: "0 1px 3px rgba(0,0,0,0.06)" };

// The Design Tokens document names Error #9B2335 / Success #2D6A4F, but the
// tenant theme layer publishes no --2a-error / --2a-success custom property, so
// there is nothing to read them from at runtime. Named here once rather than
// inlined at eight call sites; every other colour on this screen is a token.
const ERROR_INK = "#9B2335";
const SUCCESS_INK = "#2D6A4F";

function Card({ title, hint, right, children }) {
  return (
    <section className="mt-6 rounded-lg border bg-bg-card p-5" style={CARD}>
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

function Pill({ children, tone }) {
  const style =
    tone === "done"
      ? { backgroundColor: "var(--2a-bg-sidebar)", color: SUCCESS_INK }
      : { backgroundColor: "var(--2a-gold-light)", color: "var(--2a-navy)" };
  return (
    <span
      className="inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium"
      style={style}
    >
      {children}
    </span>
  );
}

function humanize(key) {
  return String(key || "")
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

// The ensemble writes its two answers into the correction ledger's
// original_value / corrected_value columns as JSON text (so null survives as
// `null` rather than the string "None"). Render them back readably.
function readValue(raw) {
  if (raw === null || raw === undefined) return "—";
  try {
    const parsed = JSON.parse(raw);
    if (parsed === null) return "null";
    if (typeof parsed === "object") return JSON.stringify(parsed);
    return String(parsed);
  } catch {
    return String(raw);
  }
}

// The ensemble record carries {primary:{model,value}, secondary:{model,value}}
// inside its notes envelope. Model names matter here: the extraction sprint
// found the fallback chain can serve BOTH calls from the same model, which
// silently collapses the cross-check. A reviewer should be able to see that.
function readModels(notes) {
  try {
    const envelope = JSON.parse(notes);
    const detail = JSON.parse(envelope.notes);
    return {
      primary: detail?.primary?.model || null,
      secondary: detail?.secondary?.model || null,
    };
  } catch {
    return { primary: null, secondary: null };
  }
}

// ─── One disagreed field: two answers, side by side, plus a manual override ──

function FieldResolver({ noteTermsId, disagreement, resolved, onResolved }) {
  const [manual, setManual] = useState("");
  const [notes, setNotes] = useState("");
  const [error, setError] = useState(null);
  const [pending, startTransition] = useTransition();

  const models = readModels(disagreement.notes);
  const primary = readValue(disagreement.primary_value);
  const secondary = readValue(disagreement.secondary_value);

  function resolve(value, source) {
    setError(null);
    startTransition(async () => {
      const res = await resolveFieldAction(
        noteTermsId, disagreement.field_name, value, source, notes || null,
      );
      if (res.ok) onResolved(res.result);
      else setError(res.error || "Could not record that resolution.");
    });
  }

  return (
    <div className="border-t border-border py-4 first:border-t-0">
      <div className="flex items-center justify-between gap-4">
        <div className="text-xs font-medium uppercase tracking-wide text-text-muted">
          {humanize(disagreement.field_name)}
        </div>
        {resolved && <Pill tone="done">Resolved</Pill>}
      </div>

      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        {[
          { label: "Primary reader", value: primary, model: models.primary, source: "primary" },
          { label: "Second reader", value: secondary, model: models.secondary, source: "secondary" },
        ].map((answer) => (
          <div
            key={answer.source}
            className="rounded-md border border-border p-3"
            style={{ backgroundColor: "var(--2a-bg)" }}
          >
            <div className="text-[11px] uppercase tracking-wide text-text-muted">
              {answer.label}
            </div>
            <div className="mt-1 text-sm font-medium text-navy">{answer.value}</div>
            {answer.model && (
              <div className="mt-1 text-[11px] text-text-muted">{answer.model}</div>
            )}
            <button
              type="button"
              disabled={pending}
              onClick={() => resolve(answer.value, answer.source)}
              className="mt-3 rounded-md bg-navy px-3 py-1.5 text-xs font-medium text-white disabled:opacity-60"
            >
              Use this
            </button>
          </div>
        ))}
      </div>

      <div className="mt-3 flex flex-wrap items-end gap-2">
        <label className="text-xs text-text-muted">
          Neither — enter the correct value
          <input
            value={manual}
            onChange={(e) => setManual(e.target.value)}
            className="mt-1 block rounded-md border border-border bg-bg-card px-3 py-2 text-sm text-text-primary outline-none focus:ring-2 focus:ring-navy"
          />
        </label>
        <button
          type="button"
          disabled={pending || !manual.trim()}
          onClick={() => resolve(manual.trim(), "manual")}
          className="rounded-md border border-border px-3 py-2 text-xs font-medium text-navy disabled:opacity-60"
        >
          Use mine
        </button>
        <label className="min-w-[14rem] flex-1 text-xs text-text-muted">
          Why (optional)
          <input
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            className="mt-1 block w-full rounded-md border border-border bg-bg-card px-3 py-2 text-sm text-text-primary outline-none focus:ring-2 focus:ring-navy"
          />
        </label>
      </div>

      {error && <p className="mt-2 text-sm" style={{ color: ERROR_INK }}>{error}</p>}
    </div>
  );
}

// ─── The grant offer — shown only at the natural moment ──────────────────────

function GrantOffer({ pairing, onGranted, onDismiss }) {
  const [notes, setNotes] = useState("");
  const [error, setError] = useState(null);
  const [pending, startTransition] = useTransition();

  function grant() {
    setError(null);
    startTransition(async () => {
      const res = await grantStpAction(pairing.cik, pairing.form_type, notes);
      if (res.ok) onGranted();
      else setError(res.error || "Could not grant straight-through processing.");
    });
  }

  return (
    <div
      className="mt-4 rounded-md border p-4"
      style={{ borderColor: "var(--2a-gold)", backgroundColor: "var(--2a-bg)" }}
    >
      <p className="text-sm text-text-primary">
        No more queued items for {pairing.filer_name} {pairing.form_type}s — grant
        straight-through processing for this issuer/form going forward?
      </p>
      <p className="mt-1 text-xs text-text-muted">
        Agreeing rows from CIK {pairing.cik} skip review. Rows where the two
        readers disagree are still queued — this never turns that off.
      </p>
      <textarea
        value={notes}
        onChange={(e) => setNotes(e.target.value)}
        rows={2}
        placeholder="Why this issuer/form is being trusted"
        className="mt-3 w-full rounded-md border border-border bg-bg-card px-3 py-2 text-sm text-text-primary outline-none focus:ring-2 focus:ring-navy"
      />
      <div className="mt-3 flex items-center gap-3">
        <button
          type="button"
          disabled={pending}
          onClick={grant}
          className="rounded-md bg-navy px-4 py-2 text-sm font-medium text-white disabled:opacity-60"
        >
          Grant straight-through processing
        </button>
        <button
          type="button"
          onClick={onDismiss}
          className="text-sm text-text-muted hover:underline"
        >
          Not now
        </button>
      </div>
      {error && <p className="mt-2 text-sm" style={{ color: ERROR_INK }}>{error}</p>}
    </div>
  );
}

// ─── The STP policy panel ────────────────────────────────────────────────────

function PolicyPanel({ policies, onChanged }) {
  const [error, setError] = useState(null);
  const [pending, startTransition] = useTransition();

  function revoke(id) {
    setError(null);
    startTransition(async () => {
      const res = await revokeStpAction(id);
      if (res.ok) onChanged();
      else setError(res.error || "Could not revoke that policy.");
    });
  }

  return (
    <Card
      title="STP Policy"
      hint="Issuer/form pairings trusted to skip review when both readers agree."
    >
      {policies.length === 0 ? (
        <p className="text-sm text-text-muted">
          No pairing is trusted yet. Every extracted row is queued.
        </p>
      ) : (
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs uppercase tracking-wide text-text-muted">
              <th className="pb-2 font-medium">Issuer</th>
              <th className="pb-2 font-medium">CIK</th>
              <th className="pb-2 font-medium">Form</th>
              <th className="pb-2 font-medium">Granted</th>
              <th className="pb-2 font-medium">Notes</th>
              <th className="pb-2" />
            </tr>
          </thead>
          <tbody>
            {policies.map((policy) => (
              <tr key={policy.id} className="border-t border-border">
                <td className="py-2 text-navy">{policy.filer_name || "—"}</td>
                <td className="py-2 text-text-muted">{policy.cik}</td>
                <td className="py-2 text-text-muted">{policy.form_type}</td>
                <td className="py-2 text-text-muted">
                  {policy.granted_at
                    ? new Date(policy.granted_at).toLocaleDateString()
                    : "—"}
                </td>
                <td className="py-2 text-text-muted">{policy.notes || "—"}</td>
                <td className="py-2 text-right">
                  <button
                    type="button"
                    disabled={pending}
                    onClick={() => revoke(policy.id)}
                    className="text-sm font-medium hover:underline disabled:opacity-60"
                    style={{ color: ERROR_INK }}
                  >
                    Revoke
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {error && <p className="mt-3 text-sm" style={{ color: ERROR_INK }}>{error}</p>}
    </Card>
  );
}

// ─── The screen ──────────────────────────────────────────────────────────────

export default function NoteTermsQueueManager({ initialPayload }) {
  const [payload, setPayload] = useState(initialPayload || { queue: [], policies: [] });
  const [selectedId, setSelectedId] = useState(null);
  const [offer, setOffer] = useState(null);
  const [banner, setBanner] = useState(null);
  const [, startTransition] = useTransition();

  const rows = payload.queue || [];
  const selected = rows.find((r) => r.id === selectedId) || null;

  function refresh() {
    startTransition(async () => {
      const res = await refreshQueueAction();
      if (res.ok) setPayload(res.payload);
      else setBanner({ type: "error", text: res.error });
    });
  }

  function onResolved(result) {
    setBanner({
      type: "ok",
      text: `${humanize(result.field)} recorded as "${result.value ?? "null"}".`,
    });
    // The grant moment: the pairing has nothing outstanding left and is not
    // already trusted. The endpoint computes this from the correction ledger.
    if (result.pairing_cleared) {
      setOffer({
        cik: result.cik,
        form_type: result.form_type,
        filer_name: result.filer_name,
      });
    }
    refresh();
  }

  const columnDefs = useMemo(
    () => [
      {
        field: "filer_name",
        headerName: "Issuer",
        cell: (value, row) => (
          <button
            type="button"
            onClick={() => setSelectedId(row.id)}
            className="text-left font-medium text-navy hover:underline"
          >
            {value}
          </button>
        ),
      },
      { field: "form_type", headerName: "Form" },
      {
        field: "filing_date",
        headerName: "Filed",
        cell: (value) => (value ? new Date(value).toLocaleDateString() : "—"),
      },
      {
        field: "disagreed_label",
        headerName: "Disagreed fields",
        cell: (_value, row) =>
          row.disagreed_fields.length === 0 ? (
            <span className="text-text-muted">
              {/* Queued with no disagreement: a clean row whose issuer/form
                  pairing simply has not been trusted yet. */}
              None — untrusted pairing
            </span>
          ) : (
            <span className="flex flex-wrap gap-1">
              {row.disagreed_fields.map((f) => (
                <Pill key={f} tone={row.unresolved_fields.includes(f) ? "open" : "done"}>
                  {humanize(f)}
                </Pill>
              ))}
            </span>
          ),
      },
      {
        field: "routing_decision",
        headerName: "Routing",
        cell: (value) => (
          <span className="text-text-muted">
            {/* NULL is a real value: the 54 rows extracted before routing
                existed were never routed, and are not pretended otherwise. */}
            {value || "not routed"}
          </span>
        ),
      },
    ],
    [],
  );

  const rowData = useMemo(
    () =>
      rows.map((row) => ({
        ...row,
        disagreed_label: row.disagreed_fields.join(", "),
      })),
    [rows],
  );

  return (
    <div>
      {banner && (
        <p
          className="mt-4 text-sm"
          style={{ color: banner.type === "error" ? ERROR_INK : SUCCESS_INK }}
        >
          {banner.text}
        </p>
      )}

      <Card
        title="Review queue"
        hint="Rows flagged by the hazard ensemble, plus any issuer/form pairing not yet trusted."
        right={
          <span className="text-sm text-text-muted">{rows.length} queued</span>
        }
      >
        <DataGrid
          gridId="note-terms-queue"
          columnDefs={columnDefs}
          rowData={rowData}
          getRowId={(row) => row.id}
          quickFilterPlaceholder="Search issuer, form, field…"
          emptyMessage="Nothing is queued for review."
        />
      </Card>

      {selected && (
        <Card
          title={`${selected.filer_name} — ${selected.form_type}`}
          hint={`Filed ${
            selected.filing_date
              ? new Date(selected.filing_date).toLocaleDateString()
              : "—"
          } · CIK ${selected.cik} · accession ${selected.accession_number}`}
          right={
            <button
              type="button"
              onClick={() => setSelectedId(null)}
              className="text-sm text-text-muted hover:underline"
            >
              Close
            </button>
          }
        >
          {selected.disagreements.length === 0 ? (
            <p className="text-sm text-text-muted">
              The two readers agreed on every hazard field. This row is queued
              only because {selected.filer_name} {selected.form_type}s have not
              been granted straight-through processing.
            </p>
          ) : (
            selected.disagreements.map((d) => (
              <FieldResolver
                key={`${d.field_name}-${d.corrected_at}`}
                noteTermsId={selected.id}
                disagreement={d}
                resolved={!selected.unresolved_fields.includes(d.field_name)}
                onResolved={onResolved}
              />
            ))
          )}

          <div className="mt-5">
            <div className="text-xs font-medium uppercase tracking-wide text-text-muted">
              Source text — characters {selected.source_char_start ?? "?"}–
              {selected.source_char_end ?? "?"} of {selected.extracted_text_length}
            </div>
            <p
              className="mt-2 whitespace-pre-wrap rounded-md border border-border p-3 text-sm text-text-primary"
              style={{ backgroundColor: "var(--2a-bg)" }}
            >
              {selected.source_excerpt || "No source offsets were recorded for this row."}
            </p>
            {selected.source_url && (
              <a
                href={selected.source_url}
                target="_blank"
                rel="noreferrer"
                className="mt-2 inline-block text-sm text-navy hover:underline"
              >
                Open the filing on EDGAR
              </a>
            )}
          </div>

          {offer &&
            selected.cik === offer.cik &&
            selected.form_type === offer.form_type && (
              <GrantOffer
                pairing={offer}
                onGranted={() => {
                  setOffer(null);
                  setBanner({
                    type: "ok",
                    text: `Straight-through processing granted for ${offer.filer_name} ${offer.form_type}s.`,
                  });
                  refresh();
                }}
                onDismiss={() => setOffer(null)}
              />
            )}
        </Card>
      )}

      <PolicyPanel policies={payload.policies || []} onChanged={refresh} />
    </div>
  );
}
