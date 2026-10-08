"use server";

// Server actions for the noteextractb1 screens (gold review, results grid).
// Same shape as lib/edgarPipelineActions.js: each wraps a server-side API call
// and returns a plain {ok, ...} result, so the client never holds a token and
// never talks to FastAPI directly (Rule 5). None carries an org_id or a
// reviewer id — the corpus is global and FastAPI records the signed-in Super
// Admin as the reviewer.

import {
  getGoldCandidates,
  getGoldNote,
  getNoteExtractionRun,
  getNoteExtractionRuns,
  putGoldValue,
  skipGoldNote,
} from "@/lib/api";

function fail(error) {
  return { ok: false, status: error.status ?? null, error: error.message };
}

export async function loadGoldCandidatesAction(params) {
  try {
    return { ok: true, payload: await getGoldCandidates(params) };
  } catch (error) {
    return fail(error);
  }
}

export async function loadGoldNoteAction(filingId) {
  try {
    return { ok: true, payload: await getGoldNote(filingId) };
  } catch (error) {
    return fail(error);
  }
}

export async function saveGoldValueAction(filingId, fieldKey, body) {
  try {
    return { ok: true, result: await putGoldValue(filingId, fieldKey, body) };
  } catch (error) {
    return fail(error);
  }
}

export async function skipGoldNoteAction(filingId, reason) {
  try {
    return { ok: true, result: await skipGoldNote(filingId, { reason }) };
  } catch (error) {
    return fail(error);
  }
}

export async function loadRunsAction(params) {
  try {
    return { ok: true, payload: await getNoteExtractionRuns(params) };
  } catch (error) {
    return fail(error);
  }
}

export async function loadRunAction(runId) {
  try {
    return { ok: true, payload: await getNoteExtractionRun(runId) };
  } catch (error) {
    return fail(error);
  }
}
