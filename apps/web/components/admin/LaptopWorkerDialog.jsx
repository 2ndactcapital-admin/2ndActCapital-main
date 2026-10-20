"use client";

/**
 * LaptopWorkerDialog — "Run on your laptop" (bulkfetchui2), opened by the Bulk
 * download tab after a Laptop run is queued, or by "Show these steps".
 *
 * Top: the laptop worker's status from the latest GET workers (the tab
 * re-checks every 5 s while this is open). Below: the numbered steps from
 * `workers.laptop_instructions`, built server-side from the
 * EDGAR_BULK_LAPTOP_WORKER_COMMAND setting. This file holds no copy of the
 * start command. Every step with a command gets a monospace box and a Copy
 * button. While a worker is running, the "start" steps collapse under "How to
 * start the worker".
 *
 * Accessible: focus moves into the dialog, Tab is kept inside it, Esc closes
 * it, and the opener (Queue run / Show these steps) gets focus back.
 * Kept free of server imports so node tests can render it (tests/edgar/).
 */

import { useEffect, useRef, useState } from "react";

import { laptopDialogStatus, laptopDialogSteps, stepHasCommand } from "@/lib/edgarBulkDownload.mjs";

const CARD = "rounded-md border border-[var(--2a-border)] bg-[var(--2a-bg-card)]";
const BUTTON =
  "rounded bg-[var(--2a-navy)] px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50";
const GHOST =
  "rounded border border-[var(--2a-border)] px-3 py-1.5 text-xs text-[var(--2a-navy)] hover:bg-[var(--2a-bg)] disabled:opacity-50";
const SUCCESS_INK = "#2D6A4F";
const GOLD = "#C5A880";

export function CopyButton({ text }) {
  const [copied, setCopied] = useState(false);
  if (!text) return null;
  return (
    <button
      type="button"
      className={GHOST}
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        } catch {
          setCopied(false);
        }
      }}
    >
      {copied ? "Copied" : "Copy"}
    </button>
  );
}

const FOCUSABLE = 'button:not([disabled]), [href], input:not([disabled]), summary, [tabindex]:not([tabindex="-1"])';

function CommandBox({ text }) {
  return (
    <div className="mt-1.5 flex items-start gap-2">
      <pre className="min-w-0 flex-1 whitespace-pre-wrap break-all rounded border border-[var(--2a-border)] bg-[var(--2a-bg)] px-2 py-1.5 font-mono text-[11px] leading-relaxed text-[var(--2a-text)]">
        {text}
      </pre>
      <CopyButton text={text} />
    </div>
  );
}

function StepItem({ step }) {
  return (
    <li className="flex gap-3 py-2">
      <span
        aria-hidden="true"
        className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full border border-[var(--2a-gold)] text-[10px] font-semibold text-[var(--2a-navy)]"
      >
        {step.n}
      </span>
      <div className="min-w-0 flex-1">
        <p className="font-medium text-[var(--2a-text)]">
          <span className="sr-only">Step {step.n}: </span>
          {step.title}
        </p>
        {step.detail && <p className="mt-0.5 text-[var(--2a-text-secondary)]">{step.detail}</p>}
        {step.keys && (
          <p className="mt-1">
            <kbd className="rounded border border-[var(--2a-border)] bg-[var(--2a-bg)] px-1.5 py-0.5 font-mono text-[11px] text-[var(--2a-text)]">
              {step.keys}
            </kbd>
          </p>
        )}
        {stepHasCommand(step) && <CommandBox text={step.command} />}
      </div>
    </li>
  );
}

export default function LaptopWorkerDialog({ dialog, workers, checking, onCheck, onViewRun, onClose, hideWhenOnline, onHideChange }) {
  const panel = useRef(null);
  const status = laptopDialogStatus(workers, { queued: dialog.source === "queue" });
  const { visible, collapsed } = laptopDialogSteps(workers, status.online);

  useEffect(() => {
    panel.current?.focus();
  }, []);

  function onKeyDown(e) {
    if (e.key === "Escape") {
      e.stopPropagation();
      onClose();
      return;
    }
    if (e.key !== "Tab" || !panel.current) return;
    const items = [...panel.current.querySelectorAll(FOCUSABLE)];
    if (!items.length) return;
    const first = items[0];
    const last = items[items.length - 1];
    if (e.shiftKey && (document.activeElement === first || document.activeElement === panel.current)) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-[rgba(15,23,42,0.25)] p-4">
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-labelledby="laptop-dialog-title"
        tabIndex={-1}
        onKeyDown={onKeyDown}
        className={`${CARD} flex max-h-[90vh] w-[36rem] max-w-full flex-col p-5 focus:outline-none`}
      >
        <h3 id="laptop-dialog-title" className="text-base font-semibold text-[var(--2a-navy)]">
          Run on your laptop
        </h3>
        <div
          role="status"
          aria-live="polite"
          className="mt-3 flex items-start gap-2 rounded border px-3 py-2 text-xs"
          style={
            status.online
              ? { borderColor: SUCCESS_INK, background: "#EEF5F1", color: SUCCESS_INK }
              : { borderColor: GOLD, background: "#FBF5E6", color: "#7A5B1F" }
          }
        >
          <span
            aria-hidden="true"
            className="mt-1 inline-block h-2 w-2 shrink-0 rounded-full"
            style={{ background: status.online ? SUCCESS_INK : GOLD }}
          />
          <span className="font-medium">{status.text}</span>
        </div>

        <div className="mt-3 min-h-0 flex-1 overflow-y-auto pr-1 text-xs">
          {!visible.length && !collapsed.length && (
            <p className="text-[var(--2a-text-muted)]">Loading the steps…</p>
          )}
          {collapsed.length > 0 && (
            <details className="rounded border border-[var(--2a-border)] px-3 py-1">
              <summary className="cursor-pointer py-1 font-medium text-[var(--2a-navy)]">How to start the worker</summary>
              <ol className="divide-y divide-[var(--2a-border)]">
                {collapsed.map((step) => (
                  <StepItem key={step.key ?? step.n} step={step} />
                ))}
              </ol>
            </details>
          )}
          <ol className="divide-y divide-[var(--2a-border)]">
            {visible.map((step) => (
              <StepItem key={step.key ?? step.n} step={step} />
            ))}
          </ol>
        </div>

        <label className="mt-3 flex items-center gap-2 text-xs text-[var(--2a-text-secondary)]">
          <input type="checkbox" checked={hideWhenOnline} onChange={(e) => onHideChange(e.target.checked)} />
          Don&apos;t show this again when the worker is running
        </label>
        <div className="mt-4 flex flex-wrap justify-end gap-2">
          <button type="button" className={GHOST} onClick={onCheck} disabled={checking}>
            {checking ? "Checking…" : "I've started it — check again"}
          </button>
          {dialog.runId && (
            <button type="button" className={GHOST} onClick={onViewRun}>
              View run
            </button>
          )}
          <button type="button" className={BUTTON} onClick={onClose}>
            Close
          </button>
        </div>
      </div>
    </div>
  );
}
