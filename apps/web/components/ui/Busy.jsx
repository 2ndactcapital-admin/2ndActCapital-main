"use client";

// CATCHALL1 Task 3 — the ONE busy indicator for slow actions (bulk actions, Recalculate, imports, uploads,
// roll-ups, generation, sync). useBusy() runs an action under the state in lib/busyAction.mjs; <BusyButton> renders
// it: disabled while pending, a spinner, "Working…" (or the caller's busyLabel), aria-busy; restored on error, with
// <BusyError> showing the message. Never copy this per screen.
import { useRef, useState } from "react";
import { IDLE, busyButtonProps, runBusy } from "@/lib/busyAction.mjs";

export function useBusy() {
  const [state, setState] = useState(IDLE);
  const ref = useRef(IDLE);
  const set = (s) => { ref.current = s; setState(s); };
  return {
    busy: state.busy,
    error: state.error,
    run: (fn) => runBusy(() => ref.current, set, fn),
    clearError: () => set({ ...ref.current, error: null }),
  };
}

export function Spinner() {
  return (
    <span aria-hidden="true" data-spinner="1"
      className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-current border-r-transparent align-[-2px]" />
  );
}

export function BusyButton({ busy = false, disabled = false, busyLabel, className, children, type = "button", onClick,
  ...rest }) {
  const p = busyButtonProps({ busy, disabled, label: children, busyLabel });
  return (
    <button type={type} className={className} disabled={p.disabled} aria-busy={p["aria-busy"]}
      data-busy-button="1" onClick={onClick} {...rest}>
      {p.spinner ? <><Spinner /> {p.text}</> : p.text}
    </button>
  );
}

export function BusyError({ error, className = "text-sm text-[#9B2335]" }) {
  if (!error) return null;
  return <span role="alert" className={className} data-busy-error="1">{error}</span>;
}
