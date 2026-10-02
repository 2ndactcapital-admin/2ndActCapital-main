"use server";

// Server actions for the EDGAR pipeline monitoring screen. Same shape as
// lib/noteTermsQueueActions.js: each wraps a server-side API call and returns a
// plain {ok, ...} result, so the client never holds a token and never talks to
// FastAPI directly (Rule 5). None carries an org_id — the manifest, the issuer
// table and the pipeline runs are global SEC reference data, and the Super
// Admin gate is enforced server-side by FastAPI from the caller's principal.

import {
  addEdgarIssuer,
  getEdgarFilings,
  getEdgarIssuers,
  getEdgarProgress,
  runEdgarPipelineNow,
  updateEdgarIssuer,
} from "@/lib/api";

function fail(error) {
  return { ok: false, status: error.status ?? null, error: error.message };
}

export async function loadFilingsAction(params) {
  try {
    return { ok: true, payload: await getEdgarFilings(params) };
  } catch (error) {
    return fail(error);
  }
}

export async function loadProgressAction() {
  try {
    return { ok: true, payload: await getEdgarProgress() };
  } catch (error) {
    return fail(error);
  }
}

export async function loadIssuersAction() {
  try {
    return { ok: true, payload: await getEdgarIssuers() };
  } catch (error) {
    return fail(error);
  }
}

export async function updateIssuerAction(cik, changes) {
  try {
    return { ok: true, result: await updateEdgarIssuer(cik, changes) };
  } catch (error) {
    return fail(error);
  }
}

export async function addIssuerAction(body) {
  try {
    return { ok: true, result: await addEdgarIssuer(body) };
  } catch (error) {
    return fail(error);
  }
}

export async function runNowAction(fetchCap) {
  try {
    return { ok: true, result: await runEdgarPipelineNow({ fetch_cap: Number(fetchCap) }) };
  } catch (error) {
    return fail(error);
  }
}
