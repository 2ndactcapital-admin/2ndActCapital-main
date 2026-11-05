"use client";

// PORTENTRY2 (MUSE-OLIVO-B11-01 / B11-03) — ONE date input for the CRM forms. A plain text box (no year spin-button
// to mangle typed digits) that accepts typed or pasted ISO / US dates; the value it submits is always ISO
// (YYYY-MM-DD) or blank. A year outside 1800–2200, a two-digit year or an impossible day shows the reason (and is
// submitted as typed, so the server refuses the save). In a <form action> it emits a hidden input named `name`; in a controlled form it
// calls onChange(iso) — only with a valid date or "". An invalid entry is never submitted as a blank.
import { useEffect, useState } from "react";
import { parseDateInput } from "@/lib/portentry2.mjs";

const INPUT =
  "w-full rounded-md border border-border bg-bg-card px-3 py-2 text-sm text-text-primary outline-none focus:ring-2 focus:ring-navy";

export default function DateInput({ name, value, defaultValue, onChange, className, placeholder = "YYYY-MM-DD",
  ariaLabel, id }) {
  const controlled = value !== undefined;
  const [text, setText] = useState(String((controlled ? value : defaultValue) || ""));
  const [error, setError] = useState(null);
  const [iso, setIso] = useState(parseDateInput(text).iso);

  useEffect(() => {
    if (controlled && value !== iso) {
      setText(String(value || ""));
      setIso(String(value || ""));
      setError(null);
    }
  }, [value]); // eslint-disable-line react-hooks/exhaustive-deps

  function accept(raw) {
    setText(raw);
    const r = parseDateInput(raw);
    setError(r.error);
    setIso(r.iso);
    if (!r.error && onChange) onChange(r.iso);
  }

  return (
    <span className="block">
      <input
        id={id}
        type="text"
        inputMode="numeric"
        autoComplete="off"
        aria-label={ariaLabel}
        aria-invalid={error ? "true" : "false"}
        placeholder={placeholder}
        value={text}
        onChange={(e) => accept(e.target.value)}
        onBlur={(e) => {
          const r = parseDateInput(e.target.value);
          if (r.iso) setText(r.iso);
        }}
        className={className || INPUT}
      />
      {/* an invalid entry is submitted as typed, so the server refuses the save with its reason — never blanked into
          a silent "clear this date" */}
      {name ? <input type="hidden" name={name} value={error ? text : iso} /> : null}
      {error ? <span className="mt-1 block text-xs text-[#9B2335]">{error}</span> : null}
    </span>
  );
}
