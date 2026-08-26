"use client";

/**
 * TriggerDetailPane — the right pane of the Triggers screen (schedulerux).
 *
 * Three modes, one component: reading a trigger, editing one, and creating one.
 * They share a form because they share a validation contract — the API applies
 * the SAME `_validate_recurrence` to a create and to an edit, so a screen that
 * built two forms would eventually let one of them offer a field the other
 * refused.
 *
 * WHY THERE IS NO CLIENT-SIDE VALIDATION HERE
 * ─────────────────────────────────────────────────────────────────────────
 * Not one cron expression, IANA zone or date ordering is checked in this file.
 * Every "that will not work" the user sees came back from the API as a real
 * 422, rendered verbatim. A second copy of the rules living in the browser
 * would drift from the ones the scheduler enforces, and the drift shows up in
 * the worst possible form: a schedule the screen accepted and the tick refuses,
 * hours later, in a log nobody is reading.
 *
 * The one thing the form does locally is disable Save while a request is in
 * flight. That is not validation.
 *
 * PAUSE IS NOT DELETE
 * ─────────────────────────────────────────────────────────────────────────
 * They are two separate controls, in two separate places, with two different
 * weights. Pause is a plain button that toggles and says what it preserved.
 * Delete is behind a confirm step that names the trigger and says the word
 * irreversible, because it is: the row goes, and occurrence_count and
 * last_fired_at go with it.
 *
 * PERMISSIONS
 * ─────────────────────────────────────────────────────────────────────────
 * `canWrite` comes from the server envelope and is the ONLY thing deciding
 * whether any write control renders. There is no `?? true` on it anywhere —
 * a missing envelope must fail closed, not restore the full editor.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import { formatDateTime, statusPillClass } from "@/lib/workflowFormat";

const CARD = { borderColor: "#ece8dd", boxShadow: "0 1px 3px rgba(0,0,0,0.06)" };
const CONTROL =
  "w-full rounded border border-[var(--2a-border)] bg-white px-2 py-1.5 text-xs text-[var(--2a-text)] focus:outline-none focus:ring-1 focus:ring-[var(--2a-gold)] disabled:bg-[var(--2a-bg)] disabled:text-[var(--2a-text-muted)]";
const EYEBROW =
  "block text-[10px] font-semibold uppercase tracking-[0.12em] text-[var(--2a-text-muted)]";

// The real IANA zone list, from the browser's own tz database — not a
// hand-maintained dropdown that would slowly disagree with the zoneinfo the API
// resolves against. Older engines without the API fall back to a free-text
// input, which the server validates exactly the same way.
function ianaZones() {
  try {
    if (typeof Intl?.supportedValuesOf === "function") {
      const zones = Intl.supportedValuesOf("timeZone");
      if (Array.isArray(zones) && zones.length) return zones;
    }
  } catch {
    // fall through
  }
  return null;
}

function browserZone() {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

/**
 * A FastAPI error detail, rendered as text.
 *
 * Two real shapes arrive: a plain string (our explicit `HTTPException(422,
 * detail="...")`) and Pydantic's array of `{loc, msg}` objects (a model
 * validator that raised). Both are surfaced; neither is rewritten. The
 * "Value error, " prefix Pydantic adds is stripped because it is noise the
 * author cannot act on — the sentence after it is the actual rule.
 */
export function formatApiError(detail, fallback = "Request failed.") {
  if (!detail) return fallback;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const messages = detail
      .map((d) => (typeof d === "string" ? d : d?.msg))
      .filter(Boolean)
      .map((m) => String(m).replace(/^Value error,\s*/, ""));
    if (messages.length) return messages.join(" · ");
  }
  return fallback;
}

// An ISO instant → the `type="date"` value (UTC calendar day), and back.
// start_date / end_date are absolute bounds, not wall-clock times: the
// recurrence's own IANA zone governs when it fires, these two only say between
// which instants it may. Day granularity is what an operator actually sets.
function toDateInput(value) {
  if (!value) return "";
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? "" : d.toISOString().slice(0, 10);
}

function fromDateInput(value, endOfDay = false) {
  if (!value) return null;
  return `${value}T${endOfDay ? "23:59:59" : "00:00:00"}Z`;
}

const EMPTY_FORM = {
  workflow_definition_id: "",
  trigger_type: "scheduled",
  schedule_cron: "0 9 * * *",
  timezone: "UTC",
  start_date: "",
  end_date: "",
  max_occurrences: "",
  is_active: true,
};

function formFromTrigger(trigger) {
  if (!trigger) return { ...EMPTY_FORM, timezone: browserZone() };
  return {
    workflow_definition_id: trigger.workflow_definition_id || "",
    trigger_type: trigger.trigger_type || "scheduled",
    schedule_cron: trigger.schedule_cron || "",
    timezone: trigger.timezone || "UTC",
    start_date: toDateInput(trigger.start_date),
    end_date: toDateInput(trigger.end_date),
    max_occurrences:
      trigger.max_occurrences == null ? "" : String(trigger.max_occurrences),
    is_active: !!trigger.is_active,
  };
}

function Field({ label, children, hint }) {
  return (
    <label className="block">
      <span className={EYEBROW}>{label}</span>
      <div className="mt-1">{children}</div>
      {hint ? (
        <span className="mt-1 block text-[10px] text-[var(--2a-text-muted)]">
          {hint}
        </span>
      ) : null}
    </label>
  );
}

function ReadRow({ label, children }) {
  return (
    <div className="flex items-baseline justify-between gap-3 border-b border-[var(--2a-border)] py-1.5 last:border-0">
      <span className={EYEBROW}>{label}</span>
      <span className="text-right text-xs text-[var(--2a-text)]">{children}</span>
    </div>
  );
}

export default function TriggerDetailPane({
  mode, // "read" | "edit" | "create"
  trigger,
  workflows = [],
  canWrite,
  onModeChange,
  onSaved,
  onDeleted,
  onCancel,
}) {
  const [form, setForm] = useState(() => formFromTrigger(trigger));
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);
  const [busy, setBusy] = useState(false);
  const [preview, setPreview] = useState(null);
  const [previewError, setPreviewError] = useState(null);
  const [confirmingDelete, setConfirmingDelete] = useState(false);

  const zones = useMemo(ianaZones, []);
  const editing = mode === "edit" || mode === "create";

  // Re-seed the form whenever the pane switches row or mode. Without this, an
  // operator who opens trigger A, edits it, then clicks trigger B would be
  // editing B's row through A's half-typed values.
  useEffect(() => {
    setForm(formFromTrigger(mode === "create" ? null : trigger));
    setError(null);
    setNotice(null);
    setPreview(null);
    setPreviewError(null);
    setConfirmingDelete(false);
  }, [mode, trigger?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const set = useCallback((key, value) => {
    setForm((f) => ({ ...f, [key]: value }));
    // Any edit invalidates a preview computed from the previous values. Leaving
    // it on screen would show five occurrences of a schedule that is no longer
    // the one in the form.
    setPreview(null);
    setPreviewError(null);
  }, []);

  const recurrenceBody = useCallback(
    () => ({
      schedule_cron: form.schedule_cron,
      timezone: form.timezone,
      start_date: fromDateInput(form.start_date),
      end_date: fromDateInput(form.end_date, true),
      max_occurrences:
        form.max_occurrences === "" ? null : Number(form.max_occurrences),
    }),
    [form],
  );

  async function runPreview() {
    setBusy(true);
    setPreview(null);
    setPreviewError(null);
    try {
      const res = await fetch("/api/admin/workflow-triggers/preview", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ...recurrenceBody(),
          // Preview an EXISTING trigger against the occurrences it has already
          // had, so a nearly-spent cap previews honestly rather than promising
          // five runs that will never happen.
          occurrence_count:
            mode === "edit" ? trigger?.occurrence_count || 0 : 0,
          count: 5,
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setPreviewError(formatApiError(data.error, "Could not preview."));
        return;
      }
      setPreview(data);
    } finally {
      setBusy(false);
    }
  }

  async function save() {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const scheduled = form.trigger_type === "scheduled";
      const url =
        mode === "create"
          ? "/api/admin/workflow-triggers"
          : `/api/admin/workflow-triggers/${trigger.id}`;
      const body =
        mode === "create"
          ? {
              workflow_definition_id: form.workflow_definition_id,
              trigger_type: form.trigger_type,
              is_active: form.is_active,
              ...(scheduled
                ? recurrenceBody()
                : { event_type: "document_confirmed" }),
            }
          : {
              is_active: form.is_active,
              ...(scheduled ? recurrenceBody() : {}),
            };
      const res = await fetch(url, {
        method: mode === "create" ? "POST" : "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(formatApiError(data.error, "Could not save the trigger."));
        return;
      }
      onSaved?.(data, mode);
    } finally {
      setBusy(false);
    }
  }

  // PAUSE / RESUME. Deliberately its own call sending ONE key: the API patches
  // sparsely, so this cannot disturb the recurrence, the bounds, the cap,
  // occurrence_count or last_fired_at. That is the guarantee the button's
  // wording makes, and it is made by what is NOT in this body.
  async function setActive(next) {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const res = await fetch(`/api/admin/workflow-triggers/${trigger.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ is_active: next }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(formatApiError(data.error, "Could not change the trigger."));
        return;
      }
      setNotice(
        next
          ? "Resumed. It will fire at its next occurrence."
          : `Paused. Its schedule, its ${data.occurrence_count} recorded firing(s) and its last-fired time are all kept.`,
      );
      onSaved?.(data, "pause");
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(`/api/admin/workflow-triggers/${trigger.id}`, {
        method: "DELETE",
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(formatApiError(data.error, "Could not delete the trigger."));
        return;
      }
      onDeleted?.(trigger.id);
    } finally {
      setBusy(false);
    }
  }

  if (!trigger && mode !== "create") {
    return (
      <div
        className="rounded-lg border bg-white p-6 text-xs text-[var(--2a-text-muted)]"
        style={CARD}
      >
        Select a trigger to see its full recurrence, its firing history and —
        with the configure permission — its controls.
      </div>
    );
  }

  const scheduled = form.trigger_type === "scheduled";

  return (
    <div className="rounded-lg border bg-white" style={CARD}>
      <div className="flex items-start justify-between gap-3 border-b border-[var(--2a-border)] px-4 py-3">
        <div>
          <h2 className="font-[Spectral,Georgia,serif] text-base text-[var(--2a-navy)]">
            {mode === "create"
              ? "New trigger"
              : trigger.workflow_name || "Trigger"}
          </h2>
          {mode !== "create" && (
            <p className="mt-0.5 text-[11px] text-[var(--2a-text-muted)]">
              {trigger.schedule_summary}
            </p>
          )}
        </div>
        {mode !== "create" && (
          <span
            className={statusPillClass(trigger.is_active ? "active" : "pending")}
          >
            {trigger.is_active ? "Active" : "Paused"}
          </span>
        )}
      </div>

      <div className="space-y-4 px-4 py-4">
        {/* ── Read facts. Always shown, for every caller. ── */}
        {mode !== "create" && (
          <div>
            <ReadRow label="Type">{trigger.trigger_type}</ReadRow>
            {trigger.trigger_type === "scheduled" ? (
              <ReadRow label="Cron">
                <code className="text-[11px]">{trigger.schedule_cron}</code>
              </ReadRow>
            ) : (
              <ReadRow label="Event">{trigger.event_type || "—"}</ReadRow>
            )}
            <ReadRow label="Times fired">{trigger.occurrence_count ?? 0}</ReadRow>
            <ReadRow label="Last fired">
              {formatDateTime(trigger.last_fired_at)}
            </ReadRow>
            <ReadRow label="Next">
              {trigger.is_active
                ? formatDateTime(trigger.next_occurrence)
                : "— (paused)"}
            </ReadRow>
            {trigger.schedule_error ? (
              <ReadRow label="Problem">
                <span className="text-[var(--2a-gold)]">
                  {trigger.schedule_error}
                </span>
              </ReadRow>
            ) : null}
            <ReadRow label="Created by">
              {trigger.created_by_name || trigger.created_by_email || "—"}
            </ReadRow>
          </div>
        )}

        {/* ── Write surface. Rendered ONLY when the server envelope allows. ── */}
        {canWrite && !editing && mode !== "create" && (
          <div className="space-y-3 border-t border-[var(--2a-border)] pt-3">
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                onClick={() => onModeChange?.("edit")}
                className="rounded border border-[var(--2a-navy)] px-3 py-1.5 text-xs text-[var(--2a-navy)] hover:bg-[var(--2a-bg)]"
              >
                Edit
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={() => setActive(!trigger.is_active)}
                className="rounded border border-[var(--2a-border)] px-3 py-1.5 text-xs text-[var(--2a-text-secondary)] hover:bg-[var(--2a-bg)] disabled:opacity-50"
              >
                {trigger.is_active ? "Pause" : "Resume"}
              </button>
            </div>
            <p className="text-[10px] leading-relaxed text-[var(--2a-text-muted)]">
              Pausing stops it firing and keeps everything — the schedule, the
              bounds, the {trigger.occurrence_count ?? 0} recorded firing(s) and
              the last-fired time. Resume picks up where it left off.
            </p>

            {/* Delete lives BELOW its own rule, at a distance from Pause, and
                behind a confirm step. Two controls that stop a trigger firing
                must not sit side by side looking equally reversible. */}
            <div className="border-t border-[var(--2a-border)] pt-3">
              {confirmingDelete ? (
                <div className="space-y-2">
                  <p className="text-xs text-[var(--2a-error,#9B2335)]">
                    Delete this trigger for{" "}
                    <strong>{trigger.workflow_name}</strong>? This is
                    irreversible — the recurrence, its{" "}
                    {trigger.occurrence_count ?? 0} recorded firing(s) and its
                    last-fired time are removed. Runs it already started are
                    kept. To stop it temporarily, pause instead.
                  </p>
                  <div className="flex gap-2">
                    <button
                      type="button"
                      disabled={busy}
                      onClick={remove}
                      className="rounded px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50"
                      style={{ background: "#9B2335" }}
                    >
                      {busy ? "Deleting…" : "Delete permanently"}
                    </button>
                    <button
                      type="button"
                      onClick={() => setConfirmingDelete(false)}
                      className="rounded border border-[var(--2a-border)] px-3 py-1.5 text-xs text-[var(--2a-text-secondary)]"
                    >
                      Keep it
                    </button>
                  </div>
                </div>
              ) : (
                <button
                  type="button"
                  onClick={() => setConfirmingDelete(true)}
                  className="text-xs underline decoration-dotted"
                  style={{ color: "#9B2335" }}
                >
                  Delete this trigger…
                </button>
              )}
            </div>
          </div>
        )}

        {/* ── The form ── */}
        {canWrite && editing && (
          <div className="space-y-3 border-t border-[var(--2a-border)] pt-3">
            {mode === "create" && (
              <>
                <Field label="Workflow">
                  <select
                    className={CONTROL}
                    value={form.workflow_definition_id}
                    onChange={(e) => set("workflow_definition_id", e.target.value)}
                  >
                    <option value="">Select a workflow…</option>
                    {workflows.map((w) => (
                      <option key={w.id} value={w.id}>
                        {w.name}
                      </option>
                    ))}
                  </select>
                </Field>
                <Field label="Trigger type">
                  <select
                    className={CONTROL}
                    value={form.trigger_type}
                    onChange={(e) => set("trigger_type", e.target.value)}
                  >
                    {/* 'scheduled' — the value the deployed rows, the API and
                        services.workflow_scheduler all use. Not 'schedule'. */}
                    <option value="scheduled">Scheduled (recurring)</option>
                    <option value="event">Event (document confirmed)</option>
                  </select>
                </Field>
              </>
            )}

            {scheduled ? (
              <>
                <Field
                  label="Cron expression"
                  hint="minute hour day-of-month month day-of-week — validated by the API, not here."
                >
                  <input
                    className={`${CONTROL} font-mono`}
                    value={form.schedule_cron}
                    onChange={(e) => set("schedule_cron", e.target.value)}
                    placeholder="0 9 * * *"
                  />
                </Field>
                <Field label="Timezone">
                  {zones ? (
                    <select
                      className={CONTROL}
                      value={form.timezone}
                      onChange={(e) => set("timezone", e.target.value)}
                    >
                      {zones.includes(form.timezone) ? null : (
                        <option value={form.timezone}>{form.timezone}</option>
                      )}
                      {zones.map((z) => (
                        <option key={z} value={z}>
                          {z}
                        </option>
                      ))}
                    </select>
                  ) : (
                    <input
                      className={CONTROL}
                      value={form.timezone}
                      onChange={(e) => set("timezone", e.target.value)}
                      placeholder="America/New_York"
                    />
                  )}
                </Field>
                <div className="grid grid-cols-2 gap-3">
                  <Field label="Start on">
                    <input
                      type="date"
                      className={CONTROL}
                      value={form.start_date}
                      onChange={(e) => set("start_date", e.target.value)}
                    />
                  </Field>
                  <Field label="End after">
                    <input
                      type="date"
                      className={CONTROL}
                      value={form.end_date}
                      onChange={(e) => set("end_date", e.target.value)}
                    />
                  </Field>
                </div>
                <Field
                  label="Maximum firings"
                  hint="Leave blank for no limit."
                >
                  <input
                    type="number"
                    min="1"
                    className={CONTROL}
                    value={form.max_occurrences}
                    onChange={(e) => set("max_occurrences", e.target.value)}
                  />
                </Field>
              </>
            ) : (
              <p className="text-xs text-[var(--2a-text-muted)]">
                This trigger starts its workflow whenever a document is
                confirmed. It has no recurrence to configure.
              </p>
            )}

            <label className="flex items-center gap-2 text-xs text-[var(--2a-text-secondary)]">
              <input
                type="checkbox"
                className="accent-[var(--2a-navy)]"
                checked={form.is_active}
                onChange={(e) => set("is_active", e.target.checked)}
              />
              Active
            </label>

            {/* ── Dry run ── */}
            {scheduled && (
              <div className="rounded border border-[var(--2a-border)] bg-[var(--2a-bg)] p-3">
                <div className="flex items-center justify-between gap-2">
                  <span className={EYEBROW}>Dry run</span>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={runPreview}
                    className="rounded border border-[var(--2a-gold)] px-2 py-1 text-[11px] text-[var(--2a-navy)] hover:bg-white disabled:opacity-50"
                  >
                    Preview next 5
                  </button>
                </div>
                {previewError && (
                  <p className="mt-2 text-[11px]" style={{ color: "#9B2335" }}>
                    {previewError}
                  </p>
                )}
                {preview && (
                  <div className="mt-2">
                    <p className="text-[11px] text-[var(--2a-text-secondary)]">
                      {preview.summary}
                    </p>
                    {preview.occurrences.length === 0 ? (
                      <p className="mt-1 text-[11px] text-[var(--2a-text-muted)]">
                        No further occurrences — an end date or a firing limit
                        stops it.
                      </p>
                    ) : (
                      <ol className="mt-1 space-y-0.5">
                        {preview.occurrences.map((o) => (
                          <li
                            key={o.utc}
                            className="text-[11px] tabular-nums text-[var(--2a-text)]"
                          >
                            {formatDateTime(o.utc)}
                          </li>
                        ))}
                      </ol>
                    )}
                    {preview.exhausted && preview.occurrences.length > 0 && (
                      <p className="mt-1 text-[11px] text-[var(--2a-text-muted)]">
                        That is the last one — an end date or a firing limit
                        stops it there.
                      </p>
                    )}
                    <p className="mt-1 text-[10px] text-[var(--2a-text-muted)]">
                      Computed by the scheduler&rsquo;s own recurrence engine.
                      Nothing has been saved.
                    </p>
                  </div>
                )}
              </div>
            )}

            <div className="flex gap-2 pt-1">
              <button
                type="button"
                disabled={busy}
                onClick={save}
                className="rounded bg-[var(--2a-navy)] px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50"
              >
                {busy ? "Saving…" : mode === "create" ? "Create trigger" : "Save changes"}
              </button>
              <button
                type="button"
                onClick={() => (mode === "create" ? onCancel?.() : onModeChange?.("read"))}
                className="rounded border border-[var(--2a-border)] px-3 py-1.5 text-xs text-[var(--2a-text-secondary)]"
              >
                Cancel
              </button>
            </div>
          </div>
        )}

        {error && (
          <p className="text-xs" style={{ color: "#9B2335" }}>
            {error}
          </p>
        )}
        {notice && (
          <p className="text-xs" style={{ color: "#2D6A4F" }}>
            {notice}
          </p>
        )}
      </div>
    </div>
  );
}
