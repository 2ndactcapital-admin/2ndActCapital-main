"use client";

import { useState, useTransition } from "react";
import { generateBriefAction } from "@/lib/profileActions";

function formatDate(iso) {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleDateString();
  } catch {
    return "";
  }
}

// Split the AI prose into its two labelled sections for styled rendering.
function splitSections(text) {
  if (!text) return [];
  const headings = ["Who They Are", "What They Need From Us"];
  const sections = [];
  let remaining = text;
  for (let i = 0; i < headings.length; i++) {
    const h = headings[i];
    const idx = remaining.indexOf(h);
    if (idx === -1) continue;
    const start = idx + h.length;
    const nextH = headings[i + 1];
    const end = nextH ? remaining.indexOf(nextH, start) : -1;
    const body = (end === -1 ? remaining.slice(start) : remaining.slice(start, end))
      .replace(/^[:\s]+/, "")
      .trim();
    sections.push({ heading: h, body });
    remaining = end === -1 ? "" : remaining.slice(end);
  }
  // Fallback: if headings weren't found, show the whole text as one block.
  if (sections.length === 0) sections.push({ heading: null, body: text });
  return sections;
}

export default function BriefTab({ entityId, entityName, initialBrief = null, canGenerate = true }) {
  const [brief, setBrief] = useState(initialBrief);
  const [pending, startTransition] = useTransition();
  const [error, setError] = useState(null);

  function generate() {
    setError(null);
    startTransition(async () => {
      const res = await generateBriefAction(entityId);
      if (res.ok) setBrief(res.brief);
      else setError(res.error);
    });
  }

  if (!brief) {
    return (
      <div className="rounded-lg border border-border bg-bg-card p-10 text-center">
        <p className="text-sm text-text-muted">
          AI synthesizes all Foundation answers and meeting notes into a concise
          advisor brief.
        </p>
        {canGenerate && (
          <button
            type="button"
            onClick={generate}
            disabled={pending}
            className="mt-4 rounded-md bg-navy px-4 py-2 text-sm font-medium text-bg-app hover:opacity-90 disabled:opacity-60"
          >
            {pending ? "Generating…" : "Generate Client Brief"}
          </button>
        )}
        {error && <p className="mt-3 text-sm text-[#9B2335]">{error}</p>}
      </div>
    );
  }

  const sections = splitSections(brief.brief_text);

  return (
    <div>
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-xl font-semibold text-navy" style={{ fontFamily: "var(--2a-font-display)" }}>
            {entityName} — Client Brief
          </h2>
          <p className="mt-1 text-xs text-text-muted">
            Generated {formatDate(brief.generated_at)}
          </p>
        </div>
        {canGenerate && (
          <button
            type="button"
            onClick={generate}
            disabled={pending}
            className="shrink-0 rounded-md border border-border px-3 py-1.5 text-sm font-medium text-navy hover:bg-border disabled:opacity-60"
          >
            {pending ? "Regenerating…" : "Regenerate"}
          </button>
        )}
      </div>

      {error && <p className="mt-2 text-sm text-[#9B2335]">{error}</p>}

      <div className="mt-6 max-w-2xl space-y-6">
        {sections.map((s, i) => (
          <section key={i}>
            {s.heading && (
              <h3
                className="text-base font-semibold text-navy"
                style={{ fontFamily: "var(--2a-font-display)" }}
              >
                {s.heading}
              </h3>
            )}
            <p className="mt-2 whitespace-pre-line text-sm leading-relaxed text-text-secondary">
              {s.body}
            </p>
          </section>
        ))}
      </div>

      {brief.key_themes && brief.key_themes.length > 0 && (
        <div className="mt-6 flex flex-wrap gap-2">
          {brief.key_themes.map((t) => (
            <span
              key={t}
              className="rounded-full bg-gold-light px-2.5 py-0.5 text-xs font-medium text-navy"
            >
              {t}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
