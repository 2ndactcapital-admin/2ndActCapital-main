import { auth0 } from "@/lib/auth0";

const API_BASE = process.env.API_BASE_URL || "http://localhost:8000";

// Build Authorization header from the user's Auth0 access token, when one is
// available. Returns an empty object if no token can be obtained so callers can
// still render (the API will respond 401 and the page shows an empty state).
async function authHeaders() {
  try {
    const result = await auth0.getAccessToken();
    const token = result?.token || result?.accessToken;
    if (token) return { Authorization: `Bearer ${token}` };
  } catch {
    // No access token available (e.g. API audience not configured yet).
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

export async function apiGet(path, searchParams) {
  const url = new URL(API_BASE + path);
  if (searchParams) {
    for (const [key, value] of Object.entries(searchParams)) {
      if (value !== undefined && value !== null && value !== "") {
        url.searchParams.set(key, value);
      }
    }
  }
  const res = await fetch(url, {
    headers: await authHeaders(),
    cache: "no-store",
  });
  if (!res.ok) throw await parseError(res);
  return res.json();
}

export async function apiSend(path, method, body) {
  const res = await fetch(API_BASE + path, {
    method,
    headers: { "Content-Type": "application/json", ...(await authHeaders()) },
    body: JSON.stringify(body),
    cache: "no-store",
  });
  if (!res.ok) throw await parseError(res);
  return res.json();
}

export const listEntities = (params) => apiGet("/api/v1/entities", params);
export const getEntity = (id) => apiGet(`/api/v1/entities/${id}`);
export const getOwnershipGraph = (id) =>
  apiGet(`/api/v1/entities/${id}/ownership-graph`);
export const createEntity = (body) => apiSend("/api/v1/entities", "POST", body);
export const updateEntity = (id, body) =>
  apiSend(`/api/v1/entities/${id}`, "PUT", body);
export const addAttribute = (id, body) =>
  apiSend(`/api/v1/entities/${id}/attributes`, "POST", body);
