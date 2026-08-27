"use client";

/**
 * RunDetailPane — the right pane of the Run History screen (schedulerhistory).
 *
 * Read-only, always: there is no write endpoint on a run, so there is no mode
 * to switch into and no control to gate. What it shows:
 *
 *   · WHAT STARTED THE RUN. For a scheduled run, the originating trigger's id
 *     and the SERVER's recurrence summary for it — the same sentence the
 *     Triggers screen prints for that same trigger, from the same
 *     `describe_schedule` the firing loop uses. For a manual run, the real
 *     person. Which of the two it is comes from `origin.kind`, read from the
 *     run's stored context; this file never guesses it from a null starter.
 *
 *   · THE STEP-BY-STEP TIMELINE, in the order the engine created the rows.
 *
 *   · FOR A HELD RUN: the real `error_detail` the engine wrote, and the real
 *     set of people who were alerted — read back from `member_todos` on the
 *     exact key `create_held_run_alerts` upserts on, NOT re-derived from "the
 *     starter plus every org admin". Re-deriving would answer "who would be
 *     alerted if it held right now", which is a different question the moment
 *     anyone joins, leaves or changes role.
 *
 * WHY DURATION IS BLANK ON MOST STEPS
 * ─────────────────────────────────────────────────────────────────────────
 * The engine writes a Service Task's `started_at` and `completed_at` in ONE
 * post-hoc UPDATE, so the interval between them is always exactly zero. That
 * zero measures nothing. The API sends `duration_measured: false` for those
 * rows and this pane prints the reason instead of the number, because a "0s"
 * in a duration column is indistinguishable from a real measurement and would
 * be read as one. A User Task's timestamps ARE two separate moments — the task
 * going active and a human completing it — so that duration is real and is
 * shown.
 */

import { useEffect, useState } from "react";
import Link from "next/link";

import {
  formatDateTime,
  formatDuration,
  personLabel,
  statusPillClass,
  NOT_MEASURED,
  NOT_MEASURED_WHY,
} from "@/lib/workflowFormat";

const CARD = { borderColor: "#ece8dd", boxShadow: "0 1px 3px rgba(0,0,0,0.06)" };
const EYEBROW =
  "block text-[10px] font-semibold uppercase tracking-[0.12em] text-[var(--2a-text-muted)]";

function Field({ label, children }) {
  return (
    <div>
      <span className={EYEBROW}>{label}</span>
      <div className="mt-0.5 text-xs text-[var(--2a-text-secondary)]">
        {children}
      </div>
    </div>
  );
}

export default function RunDetailPane({ runId, row }) {
  const [detail, setDetail] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    let cancelled = false;
    if (!runId) {
      setDetail(null);
      setError(null);
      return undefined;
    }
    setLoading(true);
    (async () => {
      try {
        const res = await fetch(`/api/admin/workflow-runs/${runId}`, {
          cache: "no-store",
        });
        const data = await res.json().catch(() => ({}));
        if (cancelled) return;
        if (!res.ok) {
          setDetail(null);
          setError(
            typeof data.error === "string" ? data.error : "Could not load run.",
          );
          return;
        }
        setDetail(data);
        setError(null);
      } catch (e) {
        if (!cancelled) setError(e.message);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [runId]);

  if (!runId) {
    return (
      <aside className="rounded-lg border bg-white p-5" style={CARD}>
        <p className="text-xs text-[var(--2a-text-muted)]">
          Select a run to see its origin, its step-by-step history and — if it
          held — the error and who was alerted.
        </p>
      </aside>
    );
  }

  const run = detail?.run;
  const steps = detail?.steps || [];
  const alerts = detail?.alerts || [];
  const origin = run?.origin || row?.origin || {};
  const scheduled = origin.kind === "scheduled";

  return (
    <aside
      className="flex flex-col gap-4 rounded-lg border bg-white p-5"
      style={CARD}
    >
      <div>
        <h2 className="text-base font-semibold text-[var(--2a-navy)]">
          {run?.workflow_name || row?.workflow_name || "Run"}
        </h2>
        <p className="mt-0.5 text-[11px] text-[var(--2a-text-muted)]">
          v{run?.version_number ?? row?.version_number ?? "—"} · {runId}
        </p>
      </div>

      {loading && !detail && (
        <p className="text-xs text-[var(--2a-text-muted)]">Loading…</p>
      )}
      {error && (
        <p className="text-xs" style={{ color: "#9B2335" }}>
          {error}
        </p>
      )}

      {run && (
        <>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Status">
              <span className={statusPillClass(run.status)}>{run.status}</span>
            </Field>
            <Field label="Duration">
              {run.duration_measured ? (
                formatDuration(run.duration_seconds)
              ) : run.completed_at ? (
                <span className="italic" title={NOT_MEASURED_WHY}>
                  {NOT_MEASURED}
                </span>
              ) : (
                "—"
              )}
            </Field>
            <Field label="Started">{formatDateTime(run.started_at)}</Field>
            <Field label="Completed">
              {run.completed_at ? formatDateTime(run.completed_at) : "—"}
            </Field>
          </div>

          {/* ── What started it ─────────────────────────────────────────── */}
          <div className="border-t border-[var(--2a-border)] pt-3">
            <span className={EYEBROW}>Started by</span>
            {scheduled ? (
              <div className="mt-1 space-y-1 text-xs">
                <p className="font-medium text-[var(--2a-navy)]">
                  {run.started_by_label}
                </p>
                <p className="text-[var(--2a-text-muted)]">
                  Trigger{" "}
                  <code className="break-all">{origin.trigger_id}</code>
                  {origin.trigger_exists === false && (
                    <span className="ml-1 text-[var(--2a-gold)]">
                      (deleted since this run)
                    </span>
                  )}
                </p>
                {origin.scheduled_occurrence && (
                  <p className="text-[var(--2a-text-muted)]">
                    Scheduled occurrence{" "}
                    {formatDateTime(origin.scheduled_occurrence)}
                  </p>
                )}
                {origin.trigger_exists && (
                  <Link
                    href="/admin/workflows/triggers"
                    className="inline-block text-[var(--2a-gold)] hover:underline"
                  >
                    Open the Triggers screen →
                  </Link>
                )}
              </div>
            ) : (
              <div className="mt-1 space-y-1 text-xs">
                <p className="font-medium text-[var(--2a-navy)]">
                  {personLabel(run.started_by_name, run.started_by_email)}
                </p>
                <p className="text-[var(--2a-text-muted)]">
                  Started manually — this run&rsquo;s context carries no trigger
                  stamp.
                </p>
              </div>
            )}
          </div>

          {/* ── Held: the real error, and the real people alerted ────────── */}
          {run.status === "held" && (
            <div className="border-t border-[var(--2a-border)] pt-3">
              <span className={EYEBROW}>Held — error detail</span>
              <p className="mt-1 rounded border border-[var(--2a-gold)] px-2 py-1.5 text-xs text-[var(--2a-gold)]">
                {run.error_detail || "No error detail was recorded."}
              </p>

              <span className={`${EYEBROW} mt-3`}>
                Alerted ({alerts.length})
              </span>
              {alerts.length === 0 ? (
                <p className="mt-1 text-xs text-[var(--2a-text-muted)]">
                  No alert todo exists for this run.
                </p>
              ) : (
                <ul className="mt-1 space-y-1">
                  {alerts.map((a) => (
                    <li
                      key={a.id}
                      className="flex items-baseline justify-between gap-2 text-xs"
                    >
                      <span className="text-[var(--2a-text-secondary)]">
                        {personLabel(a.user_name, a.user_email)}
                      </span>
                      <span className={statusPillClass(a.status)}>
                        {a.status}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
              <p className="mt-1 text-[10px] text-[var(--2a-text-muted)]">
                Read from the alert todos themselves, so this is who WAS
                notified — not who the rule would notify today.
              </p>
              {/* There is no per-user admin route to deep-link into, so this
                  links to the real roster page rather than inventing a query
                  parameter /admin/users does not read. */}
              <Link
                href="/admin/users"
                className="mt-1 inline-block text-xs text-[var(--2a-gold)] hover:underline"
              >
                Open the member roster →
              </Link>
            </div>
          )}

          {/* ── The step timeline ───────────────────────────────────────── */}
          <div className="border-t border-[var(--2a-border)] pt-3">
            <span className={EYEBROW}>Steps ({steps.length})</span>
            {steps.length === 0 ? (
              <p className="mt-1 text-xs text-[var(--2a-text-muted)]">
                This run has no governed steps.
              </p>
            ) : (
              <ol className="mt-2 space-y-3">
                {steps.map((s) => (
                  <li
                    key={s.id}
                    className="border-l-2 border-[var(--2a-border)] pl-3"
                  >
                    <div className="flex items-baseline justify-between gap-2">
                      <span className="text-xs font-medium text-[var(--2a-navy)]">
                        {s.display_name || s.step_key}
                      </span>
                      <span className={statusPillClass(s.status)}>
                        {s.status}
                      </span>
                    </div>
                    <p className="text-[10px] text-[var(--2a-text-muted)]">
                      {s.step_type} · tier {s.autonomy_tier} ·{" "}
                      {formatDateTime(s.started_at)}
                    </p>
                    <p className="text-[10px] text-[var(--2a-text-muted)]">
                      Duration:{" "}
                      {s.duration_measured ? (
                        formatDuration(s.duration_seconds)
                      ) : (
                        <span className="italic" title={NOT_MEASURED_WHY}>
                          {NOT_MEASURED} for a {s.step_type} step
                        </span>
                      )}
                    </p>
                    {s.error_detail && (
                      <p className="mt-1 text-[11px] text-[var(--2a-gold)]">
                        {s.error_detail}
                      </p>
                    )}
                    {!s.error_detail && s.result && (
                      <code className="mt-1 block break-all text-[10px] text-[var(--2a-text-muted)]">
                        {JSON.stringify(s.result)}
                      </code>
                    )}
                  </li>
                ))}
              </ol>
            )}
          </div>
        </>
      )}
    </aside>
  );
}
