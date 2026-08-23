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
  getNoteTermsQueue,
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
