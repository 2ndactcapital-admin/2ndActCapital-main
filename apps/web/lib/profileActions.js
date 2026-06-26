"use server";

import {
  upsertProfileAnswer,
  getProfileAnswers,
  startConversation,
  sendConversationMessage,
  completeConversation,
  runExtraction,
  reviewExtraction,
  generateBrief,
  getConversation,
  getExtractions,
  getBrief,
  updateEntity,
} from "@/lib/api";

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

// --- Sprint 10: conversation, extraction, brief, mode ---
export async function startConversationAction(entityId) {
  try {
    return { ok: true, conversation: await startConversation(entityId) };
  } catch (error) {
    return { ok: false, error: error.message };
  }
}

export async function sendMessageAction(entityId, message) {
  try {
    return { ok: true, result: await sendConversationMessage(entityId, message) };
  } catch (error) {
    return { ok: false, error: error.message };
  }
}

export async function completeConversationAction(entityId) {
  try {
    return { ok: true, conversation: await completeConversation(entityId) };
  } catch (error) {
    return { ok: false, error: error.message };
  }
}

export async function setProfileModeAction(entityId, mode) {
  try {
    return { ok: true, entity: await updateEntity(entityId, { profile_mode: mode }) };
  } catch (error) {
    return { ok: false, error: error.message };
  }
}

export async function runExtractionAction(entityId) {
  try {
    return { ok: true, extractions: await runExtraction(entityId) };
  } catch (error) {
    return { ok: false, error: error.message };
  }
}

export async function reviewExtractionAction(entityId, extractionId, body) {
  try {
    return { ok: true, extraction: await reviewExtraction(entityId, extractionId, body) };
  } catch (error) {
    return { ok: false, error: error.message };
  }
}

export async function generateBriefAction(entityId) {
  try {
    return { ok: true, brief: await generateBrief(entityId) };
  } catch (error) {
    return { ok: false, error: error.message };
  }
}

// Load conversation + extractions + brief for an entity (on entity switch).
export async function loadProfileExtrasAction(entityId) {
  const [conversation, extractions, brief] = await Promise.all([
    getConversation(entityId).catch(() => null),
    getExtractions(entityId).catch(() => []),
    getBrief(entityId).catch(() => null),
  ]);
  return { ok: true, conversation, extractions: extractions || [], brief };
}
