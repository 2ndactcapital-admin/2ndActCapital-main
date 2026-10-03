"use server";

// Server actions for the EDGAR pipeline monitoring screen. Same shape as
// lib/noteTermsQueueActions.js: each wraps a server-side API call and returns a
// plain {ok, ...} result, so the client never holds a token and never talks to
// FastAPI directly (Rule 5). None carries an org_id — the manifest, the issuer
// table and the pipeline runs are global SEC reference data, and the Super
// Admin gate is enforced server-side by FastAPI from the caller's principal.

import {
  addEdgarIssuer,
  copyEdgarCohort,
  createEdgarCohort,
  createEdgarTemplateStudy,
  getEdgarCohort,
  getEdgarCohortMembers,
  getEdgarCohorts,
  getEdgarInventoryRun,
  previewEdgarCohort,
  previewEdgarTemplateStudy,
  runEdgarCohort,
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

// ── Cohorts (edgarcohorts). The definition carries filters and sampling only —
// never an org_id; FastAPI refuses unknown keys.

async function wrap(fn) {
  try {
    return { ok: true, payload: await fn() };
  } catch (error) {
    return fail(error);
  }
}

export async function loadCohortsAction() {
  return wrap(() => getEdgarCohorts());
}

export async function previewCohortAction(definition) {
  return wrap(() => previewEdgarCohort({ definition }));
}

export async function createCohortAction({ name, purpose, definition }) {
  return wrap(() => createEdgarCohort({ name, purpose: purpose || null, definition }));
}

export async function previewTemplateStudyAction(body) {
  return wrap(() => previewEdgarTemplateStudy(body ?? {}));
}

export async function createTemplateStudyAction(body) {
  return wrap(() => createEdgarTemplateStudy(body ?? {}));
}

export async function loadCohortAction(id) {
  return wrap(() => getEdgarCohort(id));
}

export async function loadCohortMembersAction(id, params) {
  return wrap(() => getEdgarCohortMembers(id, params));
}

export async function copyCohortAction(id, body) {
  return wrap(() => copyEdgarCohort(id, body));
}

export async function runCohortAction(id, body) {
  return wrap(() => runEdgarCohort(id, body));
}

export async function loadInventoryRunAction(id) {
  return wrap(() => getEdgarInventoryRun(id));
}
