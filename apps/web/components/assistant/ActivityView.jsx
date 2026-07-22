"use client";

import { useEffect, useState } from "react";

const STATUS_CONFIG = {
  awaiting_review: { label: "Awaiting", bg: "bg-[var(--2a-bg-sidebar)]", text: "text-[var(--2a-text-muted)]" },
  in_progress:     { label: "In progress", bg: "bg-[#EEF4FF]", text: "text-[var(--2a-navy)]" },
  done:            { label: "Done", bg: "bg-[#E8F5E9]", text: "text-[#2D6A4F]" },
  blocked:         { label: "Blocked", bg: "bg-[#FEF3F2]", text: "text-[#9B2335]" },
  undone:          { label: "Undone", bg: "bg-[var(--2a-bg-sidebar)]", text: "text-[var(--2a-text-muted)] line-through" },
};

function StatusChip({ status }) {
  const cfg = STATUS_CONFIG[status] || { label: status, bg: "bg-[var(--2a-bg-sidebar)]", text: "text-[var(--2a-text-muted)]" };
  return (
    <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${cfg.bg} ${cfg.text}`}>
      {cfg.label}
    </span>
  );
}

export default function ActivityView() {
  const [activities, setActivities] = useState([]);
  const [loading, setLoading] = useState(true);
  const [undoing, setUndoing] = useState(null);

  async function fetchActivities() {
    try {
      const res = await fetch("/api/assistant/activities");
      if (res.ok) {
        const data = await res.json();
        setActivities(Array.isArray(data) ? data : []);
      }
    } catch (e) {
      console.error("ActivityView fetch error:", e);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    fetchActivities();
  }, []);

  async function handleUndo(id) {
    setUndoing(id);
    try {
      const res = await fetch(`/api/assistant/activity/${id}/undo`, { method: "POST" });
      if (res.ok) {
        await fetchActivities();
      }
    } catch (e) {
      console.error("Undo error:", e);
    } finally {
      setUndoing(null);
    }
  }

  if (loading) {
    return <p className="px-4 py-6 text-sm text-[var(--2a-text-muted)]">Loading…</p>;
  }

  if (!activities.length) {
    return (
      <p className="px-4 py-6 text-center text-sm text-[var(--2a-text-muted)]">
        No workflow items yet.
      </p>
    );
  }

  return (
    <ul className="divide-y divide-[var(--2a-border)]">
      {activities.map((a) => (
        <li key={a.id} className="flex flex-col gap-1 px-4 py-3">
          <div className="flex items-start justify-between gap-2">
            <span className="text-sm text-[var(--2a-text)]">{a.title || a.action_key}</span>
            <StatusChip status={a.status} />
          </div>
          {a.rationale && (
            <p className="text-xs text-[var(--2a-text-muted)]">{a.rationale}</p>
          )}
          {a.reversible && a.status === "done" && (
            <button
              onClick={() => handleUndo(a.id)}
              disabled={undoing === a.id}
              className="self-start text-xs text-[var(--2a-gold)] underline hover:no-underline disabled:opacity-50"
            >
              {undoing === a.id ? "Undoing…" : "Undo"}
            </button>
          )}
        </li>
      ))}
    </ul>
  );
}
