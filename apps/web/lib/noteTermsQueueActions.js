"use server";

// Server actions for the note-terms review queue. Same shape as
// lib/documentReviewActions.js: each wraps a server-side API call and returns a
// plain {ok, ...} result the client component can render, so the client never
// holds a token and never talks to FastAPI directly (Rule 5).
//
// None of these carries an org_id. The tables behind them are global SEC
// reference data with no tenant, and the Super Admin gate is enforced
// server-side by FastAPI from the caller's principal.

import {
  activateAiEnsemble,
  getAiModelCatalog,
  getNoteTermsQueue,
  listAiEnsembles,
  grantStpPolicy,
  resolveNoteTermsField,
  revokeStpPolicy,
} from "@/lib/api";

export async function refreshQueueAction() {
  try {
    return { ok: true, payload: await getNoteTermsQueue() };
  } catch (error) {
    return { ok: false, error: error.message };
  }
}

// `source` records WHICH answer the reviewer picked — 'primary', 'secondary',
// or 'manual' when they typed their own. It is not cosmetic: it is the only way
// to later measure which reader is right more often.
export async function resolveFieldAction(noteTermsId, field, chosenValue, source, notes) {
  try {
    const result = await resolveNoteTermsField(noteTermsId, {
      field,
      chosen_value: chosenValue,
      source,
      notes: notes || null,
    });
    return { ok: true, result };
  } catch (error) {
    return { ok: false, error: error.message };
  }
}

export async function grantStpAction(cik, formType, notes) {
  try {
    const result = await grantStpPolicy({ cik, form_type: formType, notes: notes || null });
    return { ok: true, result };
  } catch (error) {
    return { ok: false, error: error.message };
  }
}

export async function revokeStpAction(policyId) {
  try {
    const result = await revokeStpPolicy(policyId);
    return { ok: true, result };
  } catch (error) {
    return { ok: false, error: error.message };
  }
}

// The ensemble panel's two calls. A selection is never edited: activating
// creates a new immutable version server-side and retires the previous one.
export async function loadEnsembleAction(taskKey) {
  try {
    const [catalog, ensembles] = await Promise.all([
      getAiModelCatalog(),
      listAiEnsembles(taskKey),
    ]);
    return { ok: true, catalog, ensembles };
  } catch (error) {
    return { ok: false, error: error.message, status: error.status };
  }
}

export async function activateEnsembleAction(taskKey, reviewModel1, reviewModel2, comparisonModel, notes) {
  try {
    const result = await activateAiEnsemble({
      task_key: taskKey,
      review_model_1: reviewModel1,
      review_model_2: reviewModel2,
      comparison_model: comparisonModel,
      notes: notes || null,
    });
    return { ok: true, result };
  } catch (error) {
    return { ok: false, error: error.message };
  }
}
