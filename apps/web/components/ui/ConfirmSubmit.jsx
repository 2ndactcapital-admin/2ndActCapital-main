"use client";

import { useState } from "react";

// BUGFIX1 — a two-step submit button: the first press asks, the second sends
// the form with confirm=yes. Used for Archive and Submit for Review, whose
// server endpoints refuse without confirm=true as well.
export default function ConfirmSubmit({
  label, question, confirmLabel, pending = false, pendingLabel, className, name, value,
}) {
  const [asking, setAsking] = useState(false);
  if (!asking) {
    return (
      <button type="button" disabled={pending} className={className} onClick={() => setAsking(true)}>
        {pending ? pendingLabel || label : label}
      </button>
    );
  }
  return (
    <span className="inline-flex flex-wrap items-center gap-2" data-testid="confirm-step">
      <span className="text-sm text-text-secondary">{question}</span>
      <input type="hidden" name="confirm" value="yes" />
      <button
        type="submit"
        name={name}
        value={value}
        disabled={pending}
        className="rounded-md bg-navy px-3 py-1.5 text-sm font-medium text-bg-app hover:opacity-90 disabled:opacity-60"
      >
        {confirmLabel}
      </button>
      <button
        type="button"
        onClick={() => setAsking(false)}
        className="rounded-md border border-border px-3 py-1.5 text-sm text-text-secondary hover:bg-border"
      >
        Cancel
      </button>
    </span>
  );
}
