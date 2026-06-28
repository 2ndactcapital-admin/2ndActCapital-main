"use client";

import { useState } from "react";
import RenderDirective from "./RenderDirective";

// Renders a proposed WRITE action with stacked option buttons.
// navy=default option, muted=decline, POST /assistant/confirm on tap.
export default function BoundedChoice({ proposedAction, onResolved, onNavigate }) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [resolved, setResolved] = useState(null);

  if (!proposedAction) return null;
  const { options = [], rationale, params = {} } = proposedAction;

  async function choose(choiceKey) {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch("/api/assistant/confirm", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ proposed_action: proposedAction, choice_value: choiceKey }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Request failed");
      setResolved({ choiceKey, data });
      if (onResolved) onResolved(choiceKey, data);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  if (resolved) {
    const isDecline = resolved.choiceKey === "none";
    return (
      <div className="mt-2">
        {isDecline ? (
          <p className="text-xs text-[#64748B]">Noted — no action taken.</p>
        ) : (
          <>
            <p className="text-xs text-[#64748B]">Done.</p>
            <RenderDirective render={resolved.data?.render} onNavigate={onNavigate} />
          </>
        )}
      </div>
    );
  }

  return (
    <div className="mt-3 space-y-2">
      {rationale && (
        <p className="text-sm text-[#334155]">{rationale}</p>
      )}
      {params.draft_text && (
        <div className="rounded border border-[#ece8dd] bg-white px-3 py-2 text-sm text-[#334155] whitespace-pre-wrap">
          <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-[#C5A880]">Draft</p>
          {params.draft_text}
        </div>
      )}
      <div className="flex flex-col gap-1.5">
        {options.map((opt, i) => {
          const isDecline = opt.key === "none";
          return (
            <button
              key={opt.key}
              onClick={() => choose(opt.key)}
              disabled={loading}
              className={[
                "rounded px-3 py-2 text-sm font-medium transition-opacity",
                isDecline
                  ? "bg-[#F5F1EB] text-[#64748B] hover:bg-[#ece8dd]"
                  : i === 0
                  ? "bg-[#1B2B4B] text-white hover:opacity-90"
                  : "border border-[#E2E8F0] bg-white text-[#334155] hover:bg-[#FAF9F6]",
                loading && "opacity-50 cursor-not-allowed",
              ].join(" ")}
            >
              {opt.label}
            </button>
          );
        })}
      </div>
      {error && <p className="text-xs text-[#9B2335]">{error}</p>}
    </div>
  );
}
