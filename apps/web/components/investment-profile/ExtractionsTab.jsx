"use client";

import { useState, useTransition } from "react";
import {
  runExtractionAction,
  reviewExtractionAction,
} from "@/lib/profileActions";

function fieldEntries(extracted) {
  const fields = extracted?.fields || {};
  return Object.entries(fields);
}

function ExtractionCard({ entityId, extraction, onReviewed }) {
  const [expanded, setExpanded] = useState(false);
  const [pending, startTransition] = useTransition();
  const entries = fieldEntries(extraction.extracted_fields);
  const confidence = extraction.confidence ?? null;
  const answer = extraction.answer_text || "";
  const truncated = answer.length > 180 && !expanded;

  function review(accepted) {
    startTransition(async () => {
      const res = await reviewExtractionAction(entityId, extraction.id, { accepted });
      if (res.ok) onReviewed(res.extraction);
    });
  }

  if (extraction.advisor_reviewed) {
    return (
      <div
        className="rounded-lg border bg-bg-card p-4"
        style={{ borderColor: "#ece8dd" }}
      >
        <p className="text-xs text-text-muted">{extraction.question_text}</p>
        <p className="mt-1 text-sm font-medium text-navy">
          {extraction.advisor_accepted ? "Accepted ✓" : "Dismissed"}
        </p>
      </div>
    );
  }

  return (
    <div className="rounded-lg border bg-bg-card p-4" style={{ borderColor: "#ece8dd" }}>
      <p className="text-xs font-medium uppercase tracking-wide text-text-muted">
        {extraction.question_text}
      </p>
      {answer && (
        <p className="mt-1 text-sm text-text-secondary">
          {truncated ? `${answer.slice(0, 180)}…` : answer}{" "}
          {answer.length > 180 && (
            <button
              type="button"
              onClick={() => setExpanded((v) => !v)}
              className="text-xs font-medium text-navy hover:underline"
            >
              {expanded ? "less" : "more"}
            </button>
          )}
        </p>
      )}

      {entries.length > 0 ? (
        <dl className="mt-3 space-y-1.5">
          {entries.map(([k, v]) => (
            <div key={k} className="flex gap-2 text-sm">
              <dt className="min-w-[160px] text-text-muted">{k}</dt>
              <dd className="text-text-primary">
                {Array.isArray(v) ? v.join(", ") : String(v)}
              </dd>
            </div>
          ))}
        </dl>
      ) : (
        <p className="mt-3 text-sm text-text-muted">No fields extracted.</p>
      )}

      {confidence != null && (
        <div className="mt-3">
          <div className="mb-1 text-[11px] text-text-muted">
            Confidence {Math.round(confidence * 100)}%
          </div>
          <div className="h-1.5 w-full overflow-hidden rounded-full bg-border">
            <div
              className="h-full rounded-full"
              style={{ width: `${Math.round(confidence * 100)}%`, backgroundColor: "var(--2a-gold)" }}
            />
          </div>
        </div>
      )}

      <div className="mt-4 flex gap-2">
        <button
          type="button"
          onClick={() => review(true)}
          disabled={pending}
          className="rounded-md bg-navy px-3 py-1.5 text-sm font-medium text-bg-app hover:opacity-90 disabled:opacity-60"
        >
          Accept
        </button>
        <button
          type="button"
          onClick={() => review(false)}
          disabled={pending}
          className="rounded-md border border-border px-3 py-1.5 text-sm font-medium text-text-secondary hover:bg-border disabled:opacity-60"
        >
          Dismiss
        </button>
      </div>
    </div>
  );
}

export default function ExtractionsTab({ entityId, initialExtractions = [] }) {
  const [extractions, setExtractions] = useState(initialExtractions);
  const [pending, startTransition] = useTransition();
  const [error, setError] = useState(null);

  function onReviewed(updated) {
    setExtractions((prev) =>
      prev.map((e) => (e.id === updated.id ? { ...e, ...updated } : e)),
    );
  }

  function runOrRefresh() {
    setError(null);
    startTransition(async () => {
      const res = await runExtractionAction(entityId);
      if (res.ok) setExtractions(res.extractions || []);
      else setError(res.error);
    });
  }

  function acceptAll() {
    const pendingItems = extractions.filter((e) => !e.advisor_reviewed);
    startTransition(async () => {
      for (const e of pendingItems) {
        const res = await reviewExtractionAction(entityId, e.id, { accepted: true });
        if (res.ok) onReviewed(res.extraction);
      }
    });
  }

  const pendingCount = extractions.filter((e) => !e.advisor_reviewed).length;

  return (
    <div>
      <div className="flex items-center justify-between">
        <p className="text-sm text-text-muted">
          AI-extracted profile fields from Foundation answers.
        </p>
        <div className="flex gap-2">
          {pendingCount > 0 && (
            <button
              type="button"
              onClick={acceptAll}
              disabled={pending}
              className="rounded-md border border-border px-3 py-1.5 text-sm font-medium text-navy hover:bg-border disabled:opacity-60"
            >
              Accept all
            </button>
          )}
          <button
            type="button"
            onClick={runOrRefresh}
            disabled={pending}
            className="rounded-md bg-navy px-3 py-1.5 text-sm font-medium text-bg-app hover:opacity-90 disabled:opacity-60"
          >
            {pending ? "Working…" : extractions.length ? "Re-run extraction" : "Run extraction"}
          </button>
        </div>
      </div>

      {error && <p className="mt-2 text-sm text-[#9B2335]">{error}</p>}

      <div className="mt-4 space-y-3">
        {extractions.length === 0 ? (
          <div className="rounded-lg border border-border bg-bg-card p-10 text-center text-sm text-text-muted">
            No extractions yet. Run extraction after the Foundation conversation.
          </div>
        ) : (
          extractions.map((e) => (
            <ExtractionCard
              key={e.id}
              entityId={entityId}
              extraction={e}
              onReviewed={onReviewed}
            />
          ))
        )}
      </div>
    </div>
  );
}
