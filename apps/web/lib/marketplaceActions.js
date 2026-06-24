"use server";

import { redirect } from "next/navigation";
import {
  createDeal,
  indicateInterest,
  overrideInterest,
  uploadAPI,
  upsertDealScore,
  voteDeal,
} from "@/lib/api";

function empty(value) {
  const t = (value ?? "").toString().trim();
  return t === "" ? null : t;
}

function num(value) {
  const t = empty(value);
  if (t === null) return null;
  const n = Number(t.replace(/[$,%\s,]/g, ""));
  return Number.isFinite(n) ? n : null;
}

function checked(value) {
  return value === "on" || value === "true" || value === true;
}

function list(value) {
  // Split on newlines or commas, trim, drop empties.
  return (value ?? "")
    .toString()
    .split(/[\n,]/)
    .map((s) => s.trim())
    .filter(Boolean);
}

// Toggle a vote; returns the new vote summary for optimistic UI.
export async function voteDealAction(dealId, vote) {
  try {
    const summary = await voteDeal(dealId, vote);
    return { ok: true, summary };
  } catch (error) {
    return { ok: false, error: error.message };
  }
}

// Save a single scoring dimension.
export async function saveScoreAction(dealId, prevState, formData) {
  const dimension = empty(formData.get("dimension"));
  if (!dimension) return { ok: false, error: "Missing dimension." };
  try {
    const item = await upsertDealScore(dealId, {
      dimension,
      score: num(formData.get("score")) ?? 0,
      weight: num(formData.get("weight")) ?? 0,
      notes: empty(formData.get("notes")),
    });
    return { ok: true, item };
  } catch (error) {
    return { ok: false, error: error.message };
  }
}

// Indicate interest in a deal. Returns 403 compliance errors as a flag.
export async function indicateInterestAction(dealId, prevState, formData) {
  try {
    const item = await indicateInterest(dealId, {
      entity_id: empty(formData.get("entity_id")),
      amount_interest: num(formData.get("amount_interest")),
      notes: empty(formData.get("notes")),
    });
    return { ok: true, item };
  } catch (error) {
    const compliance = error.status === 403;
    return {
      ok: false,
      compliance,
      error: compliance
        ? "KYC approval and accreditation verification required to indicate interest."
        : error.message,
    };
  }
}

// Upload a deal document (multipart). Forwards the file to the API.
export async function uploadDocumentAction(dealId, prevState, formData) {
  const file = formData.get("file");
  if (!file || typeof file === "string" || file.size === 0) {
    return { ok: false, error: "Choose a file to upload." };
  }
  const fd = new FormData();
  fd.append("file", file, file.name);
  const documentType = (formData.get("document_type") ?? "").toString().trim();
  if (documentType) fd.append("document_type", documentType);
  try {
    const item = await uploadAPI(`/api/v1/deals/${dealId}/documents`, fd);
    return { ok: true, item };
  } catch (error) {
    return { ok: false, error: error.message };
  }
}

export async function overrideComplianceAction(dealId, prevState, formData) {
  try {
    await overrideInterest(dealId, {
      user_id: empty(formData.get("user_id")),
      entity_id: empty(formData.get("entity_id")),
      notes: empty(formData.get("notes")),
    });
    return { ok: true };
  } catch (error) {
    return { ok: false, error: error.message };
  }
}

// Create a new deal (staff). Redirects to the new deal on success.
export async function createDealAction(prevState, formData) {
  const name = empty(formData.get("name"));
  if (!name) return { ok: false, error: "Deal name is required." };

  let deal;
  try {
    deal = await createDeal({
      name,
      description: empty(formData.get("description")),
      asset_super_class: empty(formData.get("asset_super_class")),
      asset_class: empty(formData.get("asset_class")),
      asset_sub_category: empty(formData.get("asset_sub_category")),
      sponsor_entity_id: empty(formData.get("sponsor_entity_id")),
      sponsor_name_override: empty(formData.get("sponsor_name_override")),
      target_raise: num(formData.get("target_raise")),
      minimum_investment: num(formData.get("minimum_investment")),
      expected_return_pct: num(formData.get("expected_return_pct")),
      term_months: num(formData.get("term_months")),
      deal_date: empty(formData.get("deal_date")),
      close_date: empty(formData.get("close_date")),
      location: empty(formData.get("location")),
      highlights: list(formData.get("highlights")),
      tags: list(formData.get("tags")),
      is_featured: checked(formData.get("is_featured")),
    });
  } catch (error) {
    return { ok: false, error: error.message };
  }

  // Optionally submit for review immediately.
  if (formData.get("submit_action") === "submit") {
    try {
      const { setDealStatus } = await import("@/lib/api");
      await setDealStatus(deal.id, "submitted");
    } catch {
      // Non-fatal: deal exists as draft; surface on detail page.
    }
  }

  redirect(`/marketplace/${deal.id}`);
}
