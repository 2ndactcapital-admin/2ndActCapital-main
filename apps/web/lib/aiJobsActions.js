"use server";

// AIBUDGET1 — server actions for the Model Jobs screen. Each wraps one
// server-side API call and returns a plain {ok, ...} result, so the client
// never holds a token and never talks to FastAPI directly (Rule 5). None
// carries an org_id: ensemble jobs are platform-wide, and the Super Admin gate
// is enforced by FastAPI from the caller's principal.

import { getAiDailyRuns, getAiJobs, runAiDailyJobNow } from "@/lib/api";

export async function loadAiJobsAction() {
  try {
    return { ok: true, jobs: await getAiJobs() };
  } catch (error) {
    return { ok: false, error: error.message, status: error.status };
  }
}

export async function loadDailyRunsAction() {
  try {
    return { ok: true, daily: await getAiDailyRuns() };
  } catch (error) {
    return { ok: false, error: error.message, status: error.status };
  }
}

export async function runDailyJobNowAction(taskKey) {
  try {
    return { ok: true, result: await runAiDailyJobNow(taskKey) };
  } catch (error) {
    return { ok: false, error: error.message, status: error.status };
  }
}
