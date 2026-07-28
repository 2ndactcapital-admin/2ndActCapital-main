"use client";

/**
 * Bitemporal "as of" time-travel control.
 *
 * Mirrors the Sprint 18 ownership-editing paradigm (the inline
 * `<input type="date">` in components/crm/tabs/OwnershipTab.jsx): a native date
 * input whose value is a raw `YYYY-MM-DD` string passed straight through to the
 * API as `?as_of=`, an Apply/Clear pair, and a "Historical view" read-only
 * badge when the chosen date is in the past. Extracted here so the ownership
 * graph and the ownership tab share ONE date-picker concept rather than two.
 *
 * Props:
 *   value     — the currently-applied as_of string ("" = current/live)
 *   onApply   — (dateStr) => void, called with the YYYY-MM-DD string or ""
 */
import { useState, useEffect } from "react";

export default function AsOfPicker({ value = "", onApply }) {
  const [draft, setDraft] = useState(value);

  useEffect(() => {
    setDraft(value);
  }, [value]);

  const today = new Date().toISOString().slice(0, 10);
  const isHistorical = !!value && value < today;

  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
      <span
        style={{
          fontSize: 11,
          fontFamily: "'Hanken Grotesk', system-ui, sans-serif",
          fontWeight: 700,
          color: "var(--2a-text-muted)",
          textTransform: "uppercase",
          letterSpacing: "0.12em",
        }}
      >
        As of
      </span>
      <input
        type="date"
        value={draft}
        max={today}
        onChange={(e) => setDraft(e.target.value)}
        style={{
          borderRadius: 5,
          border: "1px solid var(--2a-border)",
          padding: "5px 8px",
          fontSize: 13,
          fontFamily: "'Hanken Grotesk', system-ui, sans-serif",
          color: "var(--2a-text)",
          background: "#fff",
        }}
      />
      <button
        type="button"
        onClick={() => onApply(draft || "")}
        style={{
          background: "var(--2a-navy)",
          color: "var(--2a-gold-light)",
          border: "none",
          borderRadius: 5,
          padding: "6px 12px",
          fontSize: 12,
          fontFamily: "'Hanken Grotesk', system-ui, sans-serif",
          fontWeight: 600,
          cursor: "pointer",
          letterSpacing: "0.04em",
        }}
      >
        Apply
      </button>
      {value && (
        <button
          type="button"
          onClick={() => {
            setDraft("");
            onApply("");
          }}
          style={{
            background: "var(--2a-bg-sidebar)",
            color: "var(--2a-text-secondary)",
            border: "1px solid #ece8dd",
            borderRadius: 5,
            padding: "6px 12px",
            fontSize: 12,
            fontFamily: "'Hanken Grotesk', system-ui, sans-serif",
            fontWeight: 600,
            cursor: "pointer",
          }}
        >
          Clear
        </button>
      )}
      {isHistorical && (
        <span
          style={{
            background: "var(--2a-gold-light)",
            color: "var(--2a-navy)",
            borderRadius: 999,
            padding: "3px 10px",
            fontSize: 11,
            fontFamily: "'Hanken Grotesk', system-ui, sans-serif",
            fontWeight: 600,
          }}
        >
          Historical view — read only
        </span>
      )}
    </div>
  );
}
