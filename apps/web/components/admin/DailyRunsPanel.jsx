"use client";

/**
 * AIBUDGET1 — the stateful wrapper around DailyRunsView: loads the envelope
 * through a server action, starts "Run now", and refreshes. The server decides
 * everything (gate, rules, budget); a missing envelope renders no controls.
 */

import { useState, useTransition } from "react";
import DailyRunsView from "@/components/admin/DailyRunsView";
import { useBusy } from "@/components/ui/Busy";
import { loadDailyRunsAction, runDailyJobNowAction } from "@/lib/aiJobsActions";

export default function DailyRunsPanel({ initial }) {
  const [data, setData] = useState(initial);
  // CATCHALL1 busy — Run now: the busy state is the shared hook; runKey only names which job is starting
  const runBusy = useBusy();
  const [runKey, setRunKey] = useState(null);
  const [message, setMessage] = useState(null);
  const [, startTransition] = useTransition();

  const envelope = data?.ok ? data.daily : null;

  function refresh() {
    startTransition(async () => setData(await loadDailyRunsAction()));
  }

  async function runNow(taskKey) {
    if (runBusy.busy) return;
    setMessage(null);
    setRunKey(taskKey);
    await runBusy.run(async () => {
      const res = await runDailyJobNowAction(taskKey);
      if (!res?.ok) throw new Error(res?.error || "Could not start the run");
      setMessage("Run started. It reads until the queue is empty or a daily budget is reached.");
    });
    setRunKey(null);
    setData(await loadDailyRunsAction());
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
      <DailyRunsView envelope={envelope} onRunNow={runNow} pendingTask={runBusy.busy ? runKey : null} message={message}
        error={runBusy.error} />
      <button type="button" onClick={refresh} className="mt-3 text-sm text-navy hover:underline">
        Refresh
      </button>
    </div>
  );
}
