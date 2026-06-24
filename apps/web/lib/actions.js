"use server";

import { redirect } from "next/navigation";
import { revalidatePath } from "next/cache";
import { createEntity, updateEntity, addAttribute } from "@/lib/api";

function emptyToNull(value) {
  if (value === null || value === undefined) return null;
  const trimmed = String(value).trim();
  return trimmed === "" ? null : trimmed;
}

function parseTags(value) {
  if (!value) return [];
  return String(value)
    .split(",")
    .map((t) => t.trim())
    .filter(Boolean);
}

// Create a new entity, then redirect to its detail page.
export async function createEntityAction(prevState, formData) {
  const body = {
    entity_type: formData.get("entity_type"),
    display_name: emptyToNull(formData.get("display_name")),
    legal_name: emptyToNull(formData.get("legal_name")),
    tax_id: emptyToNull(formData.get("tax_id")),
    date_of_birth: emptyToNull(formData.get("date_of_birth")),
    country_of_formation: emptyToNull(formData.get("country_of_formation")),
    notes: emptyToNull(formData.get("notes")),
    sub_type: emptyToNull(formData.get("sub_type")),
    status: formData.get("status") || "prospect",
    lead_source: emptyToNull(formData.get("lead_source")),
    tags: parseTags(formData.get("tags")),
    linkedin_url: emptyToNull(formData.get("linkedin_url")),
    primary_email: emptyToNull(formData.get("primary_email")),
    primary_phone: emptyToNull(formData.get("primary_phone")),
  };

  if (!body.display_name) {
    return { error: "Display name is required." };
  }

  let created;
  try {
    created = await createEntity(body);
  } catch (error) {
    return { error: error.message };
  }

  revalidatePath("/crm");
  redirect(`/crm/${created.id}`);
}

// Inline-edit an entity's details (Overview tab).
export async function updateEntityAction(id, prevState, formData) {
  const body = {
    display_name: emptyToNull(formData.get("display_name")),
    legal_name: emptyToNull(formData.get("legal_name")),
    country_of_formation: emptyToNull(formData.get("country_of_formation")),
    notes: emptyToNull(formData.get("notes")),
    sub_type: emptyToNull(formData.get("sub_type")),
    status: formData.get("status") || undefined,
    lead_source: emptyToNull(formData.get("lead_source")),
    primary_email: emptyToNull(formData.get("primary_email")),
    primary_phone: emptyToNull(formData.get("primary_phone")),
    tags: parseTags(formData.get("tags")),
  };

  try {
    await updateEntity(id, body);
  } catch (error) {
    return { error: error.message };
  }

  revalidatePath(`/crm/${id}`);
  return { ok: true };
}

// Add a key/value attribute to an entity.
export async function addAttributeAction(id, prevState, formData) {
  const attribute_key = emptyToNull(formData.get("attribute_key"));
  if (!attribute_key) {
    return { error: "Attribute key is required." };
  }
  const body = {
    attribute_key,
    attribute_value: emptyToNull(formData.get("attribute_value")),
    value_type: formData.get("value_type") || "string",
  };

  try {
    await addAttribute(id, body);
  } catch (error) {
    return { error: error.message };
  }

  revalidatePath(`/crm/${id}`);
  return { ok: true };
}
