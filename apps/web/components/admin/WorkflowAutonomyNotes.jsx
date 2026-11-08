"use client";

// S29B — the run console's unattended markers (no next/* imports, so node --test renders them directly).
import { runAutonomy, stepAutonomy } from "@/lib/workflowAutonomy.mjs";

// S29B — an unattended / shadow / platform-job / held-for-person step's marker, the delegate line and why it held.
export function StepAutonomyNote({ step }) {
  const a = stepAutonomy(step);
  if (!a) return null;
  return (
    <div className="mt-1 text-[11px]">
      <span className={a.tone === "gold" ? "rounded border border-[var(--2a-gold)] px-1.5 text-[var(--2a-navy)]"
        : "rounded border border-[var(--2a-border)] px-1.5 text-[var(--2a-text-muted)]"}>{a.badge}</span>
      {a.line && <span className="ml-2 text-[var(--2a-text-muted)]">{a.line}</span>}
      {a.reason && <p className="mt-0.5 text-[var(--2a-text-muted)]">{a.reason}</p>}
    </div>
  );
}

export function RunAutonomyNote({ run }) {
  const a = runAutonomy(run);
  if (!a) return null;
  return (
    <p className="text-[11px] text-[var(--2a-text-muted)]">
      {a.label}{a.review ? ` · ${a.review}` : ""}
    </p>
  );
}
