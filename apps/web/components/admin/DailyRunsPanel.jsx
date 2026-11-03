"use client";

/**
 * AIBUDGET1 — the stateful wrapper around DailyRunsView: loads the envelope
 * through a server action, starts "Run now", and refreshes. The server decides
 * everything (gate, rules, budget); a missing envelope renders no controls.
 */

import { useState, useTransition } from "react";
import DailyRunsView from "@/components/admin/DailyRunsView";
import { loadDailyRunsAction, runDailyJobNowAction } from "@/lib/aiJobsActions";

export default function DailyRunsPanel({ initial }) {
  const [data, setData] = useState(initial);
  const [pendingTask, setPendingTask] = useState(null);
  const [message, setMessage] = useState(null);
  const [, startTransition] = useTransition();

  const envelope = data?.ok ? data.daily : null;

  function refresh() {
    startTransition(async () => setData(await loadDailyRunsAction()));
  }

  function runNow(taskKey) {
    setMessage(null);
    setPendingTask(taskKey);
    startTransition(async () => {
      const res = await runDailyJobNowAction(taskKey);
      setPendingTask(null);
      setMessage(res.ok ? "Run started. It reads until the queue is empty or a daily budget is reached." : res.error);
      setData(await loadDailyRunsAction());
    });
  }

  if (!envelope) {
    return (
      <p className="mt-2 text-sm text-text-muted">
        {data?.status === 403 ? "Super Admin access required." : `Could not load the daily runs: ${data?.error || "no response"}`}
      </p>
    );
  }
  return (
    <div>
      <DailyRunsView envelope={envelope} onRunNow={runNow} pendingTask={pendingTask} message={message} />
      <button type="button" onClick={refresh} className="mt-3 text-sm text-navy hover:underline">
        Refresh
      </button>
    </div>
  );
}
