"use client";

/**
 * MyTasks — the dashboard's "My tasks" panel (REP2).
 *
 * Open governed human tasks for the signed-in user from GET /human-tasks/mine:
 * those they may decide (they hold the task's permission and did not open it)
 * and those they opened that are waiting on someone else. Each links to the
 * subject's own screen, where the decision is made and re-checked.
 */

import { useEffect, useState } from "react";
import { labelOf, myTasksModel } from "@/lib/execution.mjs";

const card = "rounded-[6px] border border-[#ece8dd] bg-white";

export function MyTasksView({ payload }) {
  const { actionable, waiting } = myTasksModel(payload);
  const kinds = payload?.vocabularies?.kinds;
  if (!actionable.length && !waiting.length) return null;
  const item = (t) => (
    <li key={t.id} className="flex flex-wrap items-baseline justify-between gap-2 py-1">
      <a className="text-navy hover:underline" href={t.action_path || "#"}>{t.title}</a>
      <span className="text-xs text-text-muted">{labelOf(kinds, t.kind)} · opened {String(t.opened_at || "").slice(0, 10)}</span>
    </li>
  );
  return (
    <div className={`${card} p-6`}>
      <h2 className="text-lg font-semibold text-navy">My tasks</h2>
      {actionable.length ? (
        <>
          <p className="mt-2 text-sm text-text-muted">Waiting for your decision</p>
          <ul className="mt-1 text-sm">{actionable.map(item)}</ul>
        </>
      ) : null}
      {waiting.length ? (
        <>
          <p className="mt-3 text-sm text-text-muted">You opened — waiting on a colleague</p>
          <ul className="mt-1 text-sm">{waiting.map(item)}</ul>
        </>
      ) : null}
    </div>
  );
}

export default function MyTasks() {
  const [payload, setPayload] = useState(null);
  useEffect(() => {
    let live = true;
    fetch("/api/human-tasks/mine").then((r) => (r.ok ? r.json() : null)).then((d) => { if (live) setPayload(d); })
      .catch(() => {});
    return () => { live = false; };
  }, []);
  return <MyTasksView payload={payload} />;
}
