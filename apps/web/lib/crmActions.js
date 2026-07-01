"use server";

import { fetchAPI } from "@/lib/api";

function empty(value) {
  const t = (value ?? "").toString().trim();
  return t === "" ? null : t;
}

function checked(value) {
  return value === "on" || value === "true" || value === true;
}

export async function addTaxIdAction(entityId, prevState, formData) {
  const value = empty(formData.get("value"));
  if (!value) return { ok: false, error: "Tax ID value is required." };
  try {
    const item = await fetchAPI(`/api/v1/entities/${entityId}/tax-ids`, {
      method: "POST",
      body: {
        tax_id_type: formData.get("tax_id_type"),
        tax_id_country: empty(formData.get("tax_id_country")) || "US",
        value,
        is_primary: checked(formData.get("is_primary")),
      },
    });
    return { ok: true, item };
  } catch (error) {
    return { ok: false, error: error.message };
  }
}

export async function addAddressAction(entityId, prevState, formData) {
  const street1 = empty(formData.get("street1"));
  const city = empty(formData.get("city"));
  if (!street1 || !city) return { ok: false, error: "Street and city are required." };
  const country_code = empty(formData.get("country_code"));
  const sfmRaw = formData.get("season_from_month");
  const stmRaw = formData.get("season_to_month");
  try {
    const item = await fetchAPI(`/api/v1/entities/${entityId}/addresses`, {
      method: "POST",
      body: {
        address_type: formData.get("address_type") || "primary_residence",
        street1,
        street2: empty(formData.get("street2")),
        city,
        state: empty(formData.get("state")),
        postal_code: empty(formData.get("postal_code")),
        country: country_code || empty(formData.get("country")) || "US",
        phone: empty(formData.get("phone")),
        country_code: country_code || undefined,
        region_code: empty(formData.get("region_code")) || undefined,
        is_primary: checked(formData.get("is_primary")),
        is_seasonal: checked(formData.get("is_seasonal")),
        season_from_month: sfmRaw ? parseInt(sfmRaw, 10) : undefined,
        season_to_month: stmRaw ? parseInt(stmRaw, 10) : undefined,
      },
    });
    return { ok: true, item };
  } catch (error) {
    return { ok: false, error: error.message };
  }
}

export async function addEmploymentAction(entityId, prevState, formData) {
  const employer_id = empty(formData.get("employer_id"));
  if (!employer_id) return { ok: false, error: "Select an employer entity." };
  try {
    const item = await fetchAPI(`/api/v1/entities/${entityId}/employment`, {
      method: "POST",
      body: {
        employer_id,
        title: empty(formData.get("title")),
        start_date: empty(formData.get("start_date")),
        end_date: empty(formData.get("end_date")),
        is_current: checked(formData.get("is_current")),
        notes: empty(formData.get("notes")),
      },
    });
    return { ok: true, item };
  } catch (error) {
    return { ok: false, error: error.message };
  }
}

export async function addSocialProfileAction(entityId, prevState, formData) {
  const url = empty(formData.get("url"));
  if (!url) return { ok: false, error: "URL is required." };
  try {
    const item = await fetchAPI(`/api/v1/entities/${entityId}/social-profiles`, {
      method: "POST",
      body: {
        platform: formData.get("platform"),
        url,
        is_primary: checked(formData.get("is_primary")),
      },
    });
    return { ok: true, item };
  } catch (error) {
    return { ok: false, error: error.message };
  }
}

export async function updateComplianceAction(entityId, prevState, formData) {
  try {
    const item = await fetchAPI(`/api/v1/entities/${entityId}/compliance`, {
      method: "PUT",
      body: {
        kyc_status: formData.get("kyc_status") || undefined,
        ofac_screen_status: formData.get("ofac_screen_status") || undefined,
        aml_risk_rating: formData.get("aml_risk_rating") || undefined,
        accreditation_status: formData.get("accreditation_status") || undefined,
        accreditation_basis: empty(formData.get("accreditation_basis")),
        pep_status: checked(formData.get("pep_status")),
        pep_details: empty(formData.get("pep_details")),
        notes: empty(formData.get("notes")),
      },
    });
    return { ok: true, item };
  } catch (error) {
    return { ok: false, error: error.message };
  }
}

// Typeahead search for employer selection.
export async function searchEntitiesAction(query) {
  try {
    const results = await fetchAPI("/api/v1/entities", {
      searchParams: { search: query || undefined, limit: 10 },
    });
    return { ok: true, results };
  } catch (error) {
    return { ok: false, error: error.message, results: [] };
  }
}
