"use server";

// Server actions for the Chancery Phase 6 document review screen. Each wraps a
// server-side API call (auth + org-scoping enforced server-side by FastAPI —
// org_id travels in the JWT, never in these bodies) and returns a plain
// {ok, ...} result the client component can render. Corrections and confirms are
// the real system-of-record writes; entity links reuse Phase 5's endpoints.

import {
  confirmDocument,
  getDocumentReview,
  linkDocumentEntities,
  submitFieldCorrection,
  unlinkDocumentEntity,
} from "@/lib/api";

export async function submitFieldCorrectionAction(documentId, fieldName, correctedValue, notes) {
  try {
    const result = await submitFieldCorrection(documentId, {
      field_name: fieldName,
      corrected_value: correctedValue,
      notes: notes || null,
    });
    return { ok: true, correction: result };
  } catch (error) {
    return { ok: false, error: error.message };
  }
}

export async function confirmDocumentAction(documentId) {
  try {
    const result = await confirmDocument(documentId);
    return { ok: true, document: result };
  } catch (error) {
    return { ok: false, error: error.message };
  }
}

export async function linkEntityAction(documentId, entityId, linkRole) {
  try {
    const result = await linkDocumentEntities(documentId, [entityId], linkRole);
    return { ok: true, links: result.links };
  } catch (error) {
    return { ok: false, error: error.message };
  }
}

export async function unlinkEntityAction(documentId, entityId) {
  try {
    await unlinkDocumentEntity(documentId, entityId);
    return { ok: true };
  } catch (error) {
    return { ok: false, error: error.message };
  }
}

// Refetch the whole review payload after a mutation so the screen re-renders
// from the real system-of-record (mapped_fields + links) rather than optimistic
// local state.
export async function refreshReviewAction(documentId) {
  try {
    const payload = await getDocumentReview(documentId);
    return { ok: true, payload };
  } catch (error) {
    return { ok: false, error: error.message };
  }
}
