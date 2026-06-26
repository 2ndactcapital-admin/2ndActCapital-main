"use server";

import { getEntityNotes, createEntityNote, applyNoteUpdates } from "@/lib/api";

export async function loadNotesAction(entityId) {
  try {
    return { ok: true, notes: await getEntityNotes(entityId) };
  } catch (error) {
    return { ok: false, error: error.message, notes: [] };
  }
}

export async function addNoteAction(entityId, body) {
  try {
    return { ok: true, note: await createEntityNote(entityId, body) };
  } catch (error) {
    return { ok: false, error: error.message };
  }
}

export async function applyNoteUpdatesAction(entityId, noteId, body) {
  try {
    return { ok: true, result: await applyNoteUpdates(entityId, noteId, body) };
  } catch (error) {
    return { ok: false, error: error.message };
  }
}
