import { auth0 } from "@/lib/auth0";

const API_BASE =
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

// Resolve the user's Auth0 access token for the API audience. Returns an empty
// header object if no token is available so callers can render an error/empty
// state instead of crashing.
async function authHeaders() {
  try {
    const result = await auth0.getAccessToken();
    const token = result?.token || result?.accessToken;
    if (token) return { Authorization: `Bearer ${token}` };
  } catch {
    // No token (e.g. unauthenticated render or audience not yet provisioned).
  }
  return {};
}

async function parseError(res) {
  let detail;
  try {
    detail = (await res.json())?.detail;
  } catch {
    // non-JSON body
  }
  const error = new Error(detail || `Request failed (${res.status})`);
  error.status = res.status;
  return error;
}

/**
 * Server-side fetch against the FastAPI backend with the user's bearer token.
 *
 * @param {string} path - API path, e.g. "/api/v1/entities"
 * @param {object} [options]
 * @param {string} [options.method] - HTTP method (default GET)
 * @param {any}    [options.body] - JSON-serializable request body
 * @param {object} [options.searchParams] - query params (skips empty values)
 */
export async function fetchAPI(path, options = {}) {
  const { method = "GET", body, searchParams } = options;

  const url = new URL(API_BASE + path);
  if (searchParams) {
    for (const [key, value] of Object.entries(searchParams)) {
      if (value !== undefined && value !== null && value !== "") {
        url.searchParams.set(key, value);
      }
    }
  }

  const headers = { ...(await authHeaders()) };
  if (body !== undefined) headers["Content-Type"] = "application/json";

  const res = await fetch(url, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
    cache: "no-store",
  });
  if (!res.ok) throw await parseError(res);
  return res.json();
}

/**
 * Multipart upload against the FastAPI backend with the user's bearer token.
 * Does not set Content-Type — fetch derives the multipart boundary itself.
 */
export async function uploadAPI(path, formData) {
  const url = new URL(API_BASE + path);
  const headers = { ...(await authHeaders()) };
  const res = await fetch(url, {
    method: "POST",
    headers,
    body: formData,
    cache: "no-store",
  });
  if (!res.ok) throw await parseError(res);
  return res.json();
}

// --- Entities (CRM) ---
export const listEntities = (searchParams) =>
  fetchAPI("/api/v1/entities", { searchParams });
export const getEntity = (id) => fetchAPI(`/api/v1/entities/${id}`);
export const getOwnershipGraph = (id) =>
  fetchAPI(`/api/v1/entities/${id}/ownership-graph`);
export const createEntity = (body) =>
  fetchAPI("/api/v1/entities", { method: "POST", body });
export const updateEntity = (id, body) =>
  fetchAPI(`/api/v1/entities/${id}`, { method: "PUT", body });
export const addAttribute = (id, body) =>
  fetchAPI(`/api/v1/entities/${id}/attributes`, { method: "POST", body });

// --- Investment Profile ---
export const getProfileQuestions = (category) =>
  fetchAPI("/api/v1/investment-profile/questions", {
    searchParams: category ? { category } : undefined,
  });
export const getProfileAnswers = (entityId) =>
  fetchAPI(`/api/v1/investment-profile/${entityId}/answers`);
export const upsertProfileAnswer = (entityId, body) =>
  fetchAPI(`/api/v1/investment-profile/${entityId}/answers`, {
    method: "POST",
    body,
  });
export const bulkUpsertProfileAnswers = (entityId, answers) =>
  fetchAPI(`/api/v1/investment-profile/${entityId}/answers/bulk`, {
    method: "POST",
    body: answers,
  });

// --- Config ---
export const getConfig = (category) =>
  fetchAPI("/api/v1/config", {
    searchParams: category ? { category } : undefined,
  });

// --- Taxonomy ---
export const getTaxonomy = () => fetchAPI("/api/v1/taxonomy");

// --- Marketplace ---
export const listDeals = (searchParams) =>
  fetchAPI("/api/v1/deals", { searchParams });
export const getDeal = (id) => fetchAPI(`/api/v1/deals/${id}`);
export const createDeal = (body) =>
  fetchAPI("/api/v1/deals", { method: "POST", body });
export const updateDeal = (id, body) =>
  fetchAPI(`/api/v1/deals/${id}`, { method: "PUT", body });
export const setDealStatus = (id, status) =>
  fetchAPI(`/api/v1/deals/${id}/status`, { method: "PUT", body: { status } });
export const upsertDealScore = (id, body) =>
  fetchAPI(`/api/v1/deals/${id}/scores`, { method: "POST", body });
export const voteDeal = (id, vote) =>
  fetchAPI(`/api/v1/deals/${id}/vote`, { method: "POST", body: { vote } });
export const indicateInterest = (id, body) =>
  fetchAPI(`/api/v1/deals/${id}/interest`, { method: "POST", body });
export const listDealInterest = (id) =>
  fetchAPI(`/api/v1/deals/${id}/interest`);
export const overrideInterest = (id, body) =>
  fetchAPI(`/api/v1/deals/${id}/interest/override`, { method: "POST", body });
export const getStageSummary = () =>
  fetchAPI("/api/v1/deals/stage-summary");
export const getComplianceRequests = (id) =>
  fetchAPI(`/api/v1/deals/${id}/compliance-requests`);
export const submitComplianceRequest = (id, body) =>
  fetchAPI(`/api/v1/deals/${id}/compliance-requests`, { method: "POST", body });
export const updateComplianceRequest = (id, reqId, body) =>
  fetchAPI(`/api/v1/deals/${id}/compliance-requests/${reqId}`, {
    method: "PUT",
    body,
  });
