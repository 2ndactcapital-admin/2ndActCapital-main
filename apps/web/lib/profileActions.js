"use server";

import { upsertProfileAnswer, getProfileAnswers } from "@/lib/api";

// Auto-save a single answer (called imperatively from the client on change/blur).
export async function saveAnswerAction(entityId, payload) {
  try {
    const saved = await upsertProfileAnswer(entityId, payload);
    return { ok: true, saved };
  } catch (error) {
    return { ok: false, error: error.message };
  }
}

// Load all answers for an entity (called when the entity selection changes).
export async function loadAnswersAction(entityId) {
  try {
    const answers = await getProfileAnswers(entityId);
    return { ok: true, answers };
  } catch (error) {
    return { ok: false, error: error.message, answers: [] };
  }
}
