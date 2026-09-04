import { getRequestAuthClient } from "@/lib/authServer";

const API_BASE =
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

// Resolve the user's Auth0 access token for the API audience. Returns an empty
// header object if no token is available so callers can render an error/empty
// state instead of crashing.
//
// HOST-AWARE: the client is picked from the request's own Host, so a session
// held in the Hollisworks tenant (admin.hollisworks.com) yields a token for the
// Hollisworks API audience instead of silently producing none. Resolving the
// client OUTSIDE the try keeps a Hollisworks misconfiguration fail-loud rather
// than degrading to an unauthenticated request. Every other host resolves to
// the existing 2nd Act client, unchanged.
async function authHeaders() {
  const authClient = await getRequestAuthClient();
  try {
    const result = await authClient.getAccessToken();
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
  const { method = "GET", body, searchParams, cache = "no-store" } = options;

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
    cache,
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
// Investor-capable entities for the IOI / compliance selectors (org-scoped).
export const listInvestorEntities = () =>
  fetchAPI("/api/v1/entities", {
    searchParams: { investor_only: "true", limit: 200 },
  });
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

// --- Foundation conversation (Sprint 10) ---
export const getConversation = (entityId) =>
  fetchAPI(`/api/v1/investment-profile/${entityId}/conversation`);
export const startConversation = (entityId) =>
  fetchAPI(`/api/v1/investment-profile/${entityId}/conversation/start`, {
    method: "POST",
  });
export const sendConversationMessage = (entityId, message) =>
  fetchAPI(`/api/v1/investment-profile/${entityId}/conversation/message`, {
    method: "POST",
    body: { message },
  });
export const completeConversation = (entityId) =>
  fetchAPI(`/api/v1/investment-profile/${entityId}/conversation/complete`, {
    method: "POST",
  });

// --- AI extractions (Sprint 10) ---
export const runExtraction = (entityId) =>
  fetchAPI(`/api/v1/investment-profile/${entityId}/extract`, { method: "POST" });
export const getExtractions = (entityId) =>
  fetchAPI(`/api/v1/investment-profile/${entityId}/extractions`);
export const reviewExtraction = (entityId, extractionId, body) =>
  fetchAPI(
    `/api/v1/investment-profile/${entityId}/extractions/${extractionId}/review`,
    { method: "PUT", body },
  );

// --- Client brief (Sprint 10) ---
export const getBrief = (entityId) =>
  fetchAPI(`/api/v1/investment-profile/${entityId}/brief`);
export const generateBrief = (entityId) =>
  fetchAPI(`/api/v1/investment-profile/${entityId}/brief`, { method: "POST" });

// --- Entity notes (Sprint 10) ---
export const getEntityNotes = (entityId) =>
  fetchAPI(`/api/v1/entities/${entityId}/notes`);
export const createEntityNote = (entityId, body) =>
  fetchAPI(`/api/v1/entities/${entityId}/notes`, { method: "POST", body });
export const applyNoteUpdates = (entityId, noteId, body) =>
  fetchAPI(`/api/v1/entities/${entityId}/notes/${noteId}/apply`, {
    method: "POST",
    body,
  });

// --- Config ---
export const getConfig = (category) =>
  fetchAPI("/api/v1/config", {
    searchParams: category ? { category } : undefined,
  });

// --- Reference data (Sprint 16) ---
export const getReferenceList = (listKey, parentCode) =>
  fetchAPI(`/api/v1/reference/${listKey}`, {
    searchParams: parentCode ? { parent_code: parentCode } : undefined,
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

// --- Document review ---
export const reviewDocument = (dealId, docId, body) =>
  fetchAPI(`/api/v1/deals/${dealId}/documents/${docId}/review`, {
    method: "PUT",
    body,
  });

// --- AI summary ---
export const getAISummary = (dealId) =>
  fetchAPI(`/api/v1/deals/${dealId}/ai-summary`);
export const generateAISummary = (dealId) =>
  fetchAPI(`/api/v1/deals/${dealId}/ai-summary`, { method: "POST" });

// --- Deal stage ---
export const updateDealStage = (dealId, body) =>
  fetchAPI(`/api/v1/deals/${dealId}/stage`, { method: "PUT", body });

// --- Member investments ---
export const getMemberInvestments = (dealId) =>
  fetchAPI(`/api/v1/deals/${dealId}/member-investments`);
export const updateMemberInvestmentStage = (dealId, userId, body) =>
  fetchAPI(`/api/v1/deals/${dealId}/member-investments/${userId}/stage`, {
    method: "POST",
    body,
  });

// --- Portfolio ---
export const getMyInvestments = () =>
  fetchAPI("/api/v1/portfolio/my-investments");
export const getPortfolioSummary = () =>
  fetchAPI("/api/v1/portfolio/summary");

// --- Portfolio targets (entity-centric, Sprint 8) ---
export const getEntityTargets = (entityId) =>
  fetchAPI("/api/v1/portfolio/targets", { searchParams: { entity_id: entityId } });
export const setEntityTargets = (entityId, items) =>
  fetchAPI("/api/v1/portfolio/targets", {
    method: "PUT",
    body: { items },
    searchParams: { entity_id: entityId },
  });
export const clearEntityTarget = (entityId, taxonomyKey) =>
  fetchAPI("/api/v1/portfolio/targets", {
    method: "DELETE",
    searchParams: { entity_id: entityId, taxonomy_key: taxonomyKey },
  });
export const getEntityAllocations = (entityId) =>
  fetchAPI("/api/v1/portfolio/allocations", {
    searchParams: entityId ? { entity_id: entityId } : undefined,
  });

// --- Deal taxonomy placement (Sprint 8) ---
export const getDealTaxonomyPlacement = (dealId) =>
  fetchAPI(`/api/v1/deals/${dealId}/taxonomy-placement`);

// --- Current user (Sprint 9) ---
export const getMe = () => fetchAPI("/api/v1/users/me");

// --- Notifications (Sprint 9) ---
export const getNotifications = (searchParams) =>
  fetchAPI("/api/v1/notifications", { searchParams });
export const getNotificationCount = () =>
  fetchAPI("/api/v1/notifications/count");
export const markNotificationRead = (id) =>
  fetchAPI(`/api/v1/notifications/${id}/read`, { method: "PUT" });
export const markAllNotificationsRead = () =>
  fetchAPI("/api/v1/notifications/read-all", { method: "PUT" });

// --- Admin: user / role management (Sprint 9) ---
export const getAdminUsers = (searchParams) =>
  fetchAPI("/api/v1/admin/users", { searchParams });
export const getAdminRoles = () => fetchAPI("/api/v1/admin/roles");
export const assignUserRole = (userId, roleId) =>
  fetchAPI(`/api/v1/admin/users/${userId}/role`, {
    method: "PUT",
    body: { role_id: roleId },
  });

// --- Admin: account lifecycle (user-management sprint) ---
// NOTE, as with createInvite below: no `org_id` in any body. The backend
// resolves the caller's org from the request context and refuses a target
// outside it (404), so an admin can only ever act on their own members.
//
// `deleteUser` is named for the verb the UI offers, but the backend ANONYMIZES:
// users.id has 92 FK dependents across 69 public tables, 89 of them ON DELETE
// NO ACTION, so a real row delete is impossible without destroying the audit
// trail (and the 3 that cascade would take votes/interest with them). The
// response carries `hard_deleted: false, anonymized: true` — the screen shows
// the user what actually happened rather than the word "deleted" alone.
export const updateAdminUser = (userId, { fullName }) =>
  fetchAPI(`/api/v1/admin/users/${userId}`, {
    method: "PATCH",
    body: { full_name: fullName },
  });
export const deactivateUser = (userId) =>
  fetchAPI(`/api/v1/admin/users/${userId}/deactivate`, { method: "POST" });
export const reactivateUser = (userId) =>
  fetchAPI(`/api/v1/admin/users/${userId}/reactivate`, { method: "POST" });
export const deleteUser = (userId) =>
  fetchAPI(`/api/v1/admin/users/${userId}`, { method: "DELETE" });
export const getUserManagementSettings = () =>
  fetchAPI("/api/v1/admin/users/settings");

// --- Admin: invites (Multi-tenant Sprint 2 backend, wired to the UI here) ---
// POST /admin/invites is what actually creates the users row. NOTE the body:
// email / full_name / role ONLY. `org_id` is deliberately absent — the backend
// takes it from the caller's own request context via get_org_id(), never from
// the body (standing multi-tenant rule). Adding it here would be the bug.
// `profile_id` is OPTIONAL and additive — the account role is still carried by
// `role`, which stays required. The backend validates the profile against the
// caller's OWN org, so passing one from another tenant is a 404, not a grant.
export const createInvite = ({ email, fullName, role, profileId }) =>
  fetchAPI("/api/v1/admin/invites", {
    method: "POST",
    body: {
      email,
      full_name: fullName || null,
      role: role || "member",
      profile_id: profileId || null,
    },
  });
export const getInvites = (status) =>
  fetchAPI("/api/v1/admin/invites", { searchParams: { status } });

/**
 * Classify an invite token — PRE-AUTH, so deliberately NOT via fetchAPI.
 *
 * An invitee has no session, which is the whole point of an invite. Going
 * through fetchAPI would call getRequestAuthClient(), and on
 * admin.hollisworks.com that fail-loud client THROWS when the Hollisworks env
 * vars are unset — breaking a page that needs no token at all. So this is a
 * plain fetch against the public endpoint, the same shape lib/tenant.js uses
 * for /tenant/resolve, forwarding the browser's Host for the cross-tenant check.
 *
 * Never throws: an unreachable API yields `status: "unreachable"`, which the
 * page renders as its own honest message rather than a blank error.
 */
export async function validateInvite({ inviteToken, host }) {
  try {
    const url = new URL(API_BASE + "/api/v1/enroll/validate");
    if (inviteToken) url.searchParams.set("invite_token", inviteToken);
    if (host) url.searchParams.set("host", host);
    const res = await fetch(url, { cache: "no-store" });
    if (!res.ok) return { status: "unreachable", message: null };
    return await res.json();
  } catch {
    return { status: "unreachable", message: null };
  }
}

/**
 * Claim the pending invite row for the just-authenticated identity.
 *
 * Needs the user's bearer token, so it goes through the authenticated path —
 * but deliberately NOT through fetchAPI's throw-on-!ok behaviour. Every failure
 * here already comes back with the backend's own `{status, message}` describing
 * precisely what went wrong; fetchAPI would reduce that to an Error whose only
 * survivor is the string, forcing the page to guess the reason back out of the
 * HTTP code (where expired and revoked are both 400). Returning the body lets
 * the page render the right message with no inference at all.
 *
 * Never throws: an unreachable API yields `status: "unreachable"`.
 */
export async function acceptInvite({ inviteToken, host }) {
  try {
    const res = await fetch(new URL(API_BASE + "/api/v1/enroll/accept"), {
      method: "POST",
      headers: { ...(await authHeaders()), "Content-Type": "application/json" },
      body: JSON.stringify({ invite_token: inviteToken, host: host || null }),
      cache: "no-store",
    });
    const body = await res.json().catch(() => ({}));
    if (body?.status) return { ...body, ok: res.ok };
    return { status: "unreachable", message: body?.detail || null, ok: false };
  } catch {
    return { status: "unreachable", message: null, ok: false };
  }
}
export const revokeInvite = (inviteId) =>
  fetchAPI(`/api/v1/admin/invites/${inviteId}/revoke`, { method: "POST" });

// --- Admin: staff teams + entity assignments (SOC Phase 2) ---
// These populate the data the staff-visibility resolver reads. They do NOT
// change any existing endpoint's visibility behavior.
export const getStaffTeams = () => fetchAPI("/api/v1/admin/staff/teams");
export const createStaffTeam = (body) =>
  fetchAPI("/api/v1/admin/staff/teams", { method: "POST", body });
export const addStaffTeamMember = (teamId, userId) =>
  fetchAPI(`/api/v1/admin/staff/teams/${teamId}/members`, {
    method: "POST",
    body: { user_id: userId },
  });
export const removeStaffTeamMember = (teamId, userId) =>
  fetchAPI(`/api/v1/admin/staff/teams/${teamId}/members/${userId}`, {
    method: "DELETE",
  });
export const getStaffAssignments = () =>
  fetchAPI("/api/v1/admin/staff/assignments");
export const createStaffAssignment = (body) =>
  fetchAPI("/api/v1/admin/staff/assignments", { method: "POST", body });
export const deleteStaffAssignment = (id) =>
  fetchAPI(`/api/v1/admin/staff/assignments/${id}`, { method: "DELETE" });

// --- Profiles + permission sets (SOC Phase A) ---
// Manage the additive profile-permission layer (services.profiles). Org Admin
// (own org) or Super Admin, enforced server-side. These do NOT touch roles.
export const getActionPermissions = () =>
  fetchAPI("/api/v1/admin/permissions");
export const getProfiles = () => fetchAPI("/api/v1/admin/profiles");
export const createProfile = (body) =>
  fetchAPI("/api/v1/admin/profiles", { method: "POST", body });
export const toggleProfilePermission = (profileId, permissionKey, granted) =>
  fetchAPI(`/api/v1/admin/profiles/${profileId}/permissions`, {
    method: "PUT",
    body: { permission_key: permissionKey, granted },
  });
export const deleteProfile = (profileId) =>
  fetchAPI(`/api/v1/admin/profiles/${profileId}`, { method: "DELETE" });

export const getPermissionSets = () =>
  fetchAPI("/api/v1/admin/permission-sets");
export const createPermissionSet = (body) =>
  fetchAPI("/api/v1/admin/permission-sets", { method: "POST", body });
export const togglePermissionSetPermission = (setId, permissionKey, granted) =>
  fetchAPI(`/api/v1/admin/permission-sets/${setId}/permissions`, {
    method: "PUT",
    body: { permission_key: permissionKey, granted },
  });
export const deletePermissionSet = (setId) =>
  fetchAPI(`/api/v1/admin/permission-sets/${setId}`, { method: "DELETE" });
export const assignPermissionSetToUser = (setId, userId) =>
  fetchAPI(`/api/v1/admin/permission-sets/${setId}/users`, {
    method: "POST",
    body: { user_id: userId },
  });
export const removePermissionSetFromUser = (setId, userId) =>
  fetchAPI(`/api/v1/admin/permission-sets/${setId}/users/${userId}`, {
    method: "DELETE",
  });
export const setUserProfile = (userId, profileId) =>
  fetchAPI(`/api/v1/admin/users/${userId}/profile`, {
    method: "PUT",
    body: { profile_id: profileId },
  });

// --- Restricted-access accounts (SOC Phase 4) ---
// Populate/read the restriction data the unified filter_restricted reads.
// Super Admin only, enforced server-side. Does NOT change enforcement.
export const getRestrictedAccounts = () =>
  fetchAPI("/api/v1/admin/restricted");
export const setEntityRestricted = (entityId, body) =>
  fetchAPI(`/api/v1/admin/restricted/${entityId}`, { method: "POST", body });
export const grantRestrictedAccess = (entityId, body) =>
  fetchAPI(`/api/v1/admin/restricted/${entityId}/grants`, {
    method: "POST",
    body,
  });
export const revokeRestrictedAccess = (entityId, userId) =>
  fetchAPI(`/api/v1/admin/restricted/${entityId}/grants/${userId}`, {
    method: "DELETE",
  });

// --- Trading authority grants (SOC Phase 5) ---
// Assign a user's per-entity trading-authority tier (inquiry|limited|full).
// Super Admin only, enforced server-side. Feeds the maker-checker + tier
// enforcement engine (services.trading_authority); does NOT enforce here.
export const getTradingAuthorityGrants = () =>
  fetchAPI("/api/v1/admin/trading-authority");
export const upsertTradingAuthorityGrant = (body) =>
  fetchAPI("/api/v1/admin/trading-authority", { method: "POST", body });
export const revokeTradingAuthorityGrant = (entityId, userId) =>
  fetchAPI(`/api/v1/admin/trading-authority/${entityId}/${userId}`, {
    method: "DELETE",
  });

// --- SPV Manager (Sprint 12) ---
export const listSPVs = (searchParams) =>
  fetchAPI("/api/v1/spvs", { searchParams });
export const getSPV = (id) => fetchAPI(`/api/v1/spvs/${id}`);
export const createSPV = (body) =>
  fetchAPI("/api/v1/spvs", { method: "POST", body });
export const updateSPV = (id, body) =>
  fetchAPI(`/api/v1/spvs/${id}`, { method: "PATCH", body });
export const transitionSPVStatus = (id, body) =>
  fetchAPI(`/api/v1/spvs/${id}/status`, { method: "POST", body });
export const setSPVFormEntity = (id, body) =>
  fetchAPI(`/api/v1/spvs/${id}/form-entity`, { method: "POST", body });
export const subscribeSPV = (id, body) =>
  fetchAPI(`/api/v1/spvs/${id}/subscriptions`, { method: "POST", body });
export const amendSubscription = (spvId, subId, body) =>
  fetchAPI(`/api/v1/spvs/${spvId}/subscriptions/${subId}`, {
    method: "PATCH",
    body,
  });
export const getSPVCapTable = (id) =>
  fetchAPI(`/api/v1/spvs/${id}/captable`);
export const listSPVDocuments = (id) =>
  fetchAPI(`/api/v1/spvs/${id}/documents`);
export const getSPVHistory = (id) =>
  fetchAPI(`/api/v1/spvs/${id}/history`);

// --- Investment (deal) classes + roll-up (Sprint 23) ---
export const getDealClasses = (dealId) =>
  fetchAPI(`/api/v1/deals/${dealId}/classes`);
export const getDealRollup = (dealId) =>
  fetchAPI(`/api/v1/deals/${dealId}/rollup`);

// --- SPV Transactions (Sprint 14) ---
export const listSPVTransactions = (spvId) =>
  fetchAPI(`/api/v1/spvs/${spvId}/transactions`);
export const createSPVTransaction = (spvId, body) =>
  fetchAPI(`/api/v1/spvs/${spvId}/transactions`, { method: "POST", body });
export const updateSPVTransaction = (spvId, txnId, body) =>
  fetchAPI(`/api/v1/spvs/${spvId}/transactions/${txnId}`, {
    method: "PATCH",
    body,
  });
export const allocateSPVTransaction = (spvId, txnId) =>
  fetchAPI(`/api/v1/spvs/${spvId}/transactions/${txnId}/allocate`, {
    method: "POST",
    body: {},
  });
export const postSPVTransaction = (spvId, txnId) =>
  fetchAPI(`/api/v1/spvs/${spvId}/transactions/${txnId}/post`, {
    method: "POST",
    body: {},
  });
export const voidSPVTransaction = (spvId, txnId) =>
  fetchAPI(`/api/v1/spvs/${spvId}/transactions/${txnId}/void`, {
    method: "POST",
    body: {},
  });
export const listSPVAllocations = (spvId, txnId) =>
  fetchAPI(`/api/v1/spvs/${spvId}/transactions/${txnId}/allocations`);
export const getSPVLedger = (spvId) =>
  fetchAPI(`/api/v1/spvs/${spvId}/ledger`);

// --- Entity Documents (Sprint 17) ---
export const listEntityDocuments = (entityId, searchParams) =>
  fetchAPI(`/api/v1/entities/${entityId}/documents`, { searchParams });
export const patchEntityDocument = (entityId, docId, body) =>
  fetchAPI(`/api/v1/entities/${entityId}/documents/${docId}`, { method: "PATCH", body });
export const getDocumentDownloadUrl = (entityId, docId) =>
  fetchAPI(`/api/v1/entities/${entityId}/documents/${docId}/download`);

// --- Ownership (Sprint 18) ---
export const getEntityOwnership = (entityId, asOf) =>
  fetchAPI(`/api/v1/entities/${entityId}/ownership`, {
    searchParams: asOf ? { as_of: asOf } : undefined,
  });
export const createEntityOwnership = (entityId, body) =>
  fetchAPI(`/api/v1/entities/${entityId}/ownership`, { method: "POST", body });
export const amendOwnership = (relId, body) =>
  fetchAPI(`/api/v1/entity-relationships/${relId}/ownership`, { method: "PATCH", body });
export const deleteOwnership = (relId) =>
  fetchAPI(`/api/v1/entity-relationships/${relId}/ownership`, { method: "DELETE" });
export const getOwnershipHistory = (entityId) =>
  fetchAPI(`/api/v1/entities/${entityId}/ownership/history`);

// --- Workflow Manager (Phase 3) ---
// Library + diagram editor. Org Admin (own org) or Super Admin, enforced
// server-side by the FastAPI /admin/workflows gate. org_id travels in the JWT,
// never in a request body.
export const getWorkflows = () => fetchAPI("/api/v1/admin/workflows");
export const getWorkflow = (id) => fetchAPI(`/api/v1/admin/workflows/${id}`);
export const createWorkflow = (body) =>
  fetchAPI("/api/v1/admin/workflows", { method: "POST", body });
export const saveWorkflowVersion = (id, body) =>
  fetchAPI(`/api/v1/admin/workflows/${id}/versions`, { method: "POST", body });

// --- Workflow Manager — read-only consoles ---
// Run History, Scheduler/Routine Viewer, Version History. Org Admin sees their
// own org; Super Admin sees across all orgs (enforced server-side).
//
// Run History returns an ENVELOPE — {rows, permissions, filters} — not a bare
// list. `filters` echoes back what the server actually applied, including the
// instant it resolved a named period to, so the screen can label the window it
// is showing without computing a second boundary of its own.
//
// The status and period filters are QUERY PARAMETERS, applied in SQL. They are
// not grid filters: "runs in the last 7 days" is a claim about the whole table,
// and filtering a 200-row page in the browser would quietly answer a different
// question.
export const getWorkflowRuns = ({ status, period, since, until } = {}) => {
  const query = new URLSearchParams();
  if (status) query.set("status", status);
  if (period) query.set("period", period);
  if (since) query.set("since", since);
  if (until) query.set("until", until);
  const suffix = query.toString();
  return fetchAPI(
    `/api/v1/admin/workflow-runs${suffix ? `?${suffix}` : ""}`,
  );
};
export const getWorkflowRun = (runId) =>
  fetchAPI(`/api/v1/admin/workflow-runs/${runId}`);
// The Triggers screen (schedulerux). Returns an ENVELOPE — {rows, permissions}
// — not a bare list; `permissions.can_write` is what decides whether the screen
// renders a create / edit / pause / delete control, and there is deliberately
// no client-side default for it.
//
// READ is gated by view_workflow_runs OR configure_workflow_triggers; every
// WRITE below needs configure_workflow_triggers. Both are enforced server-side.
export const getWorkflowTriggers = () =>
  fetchAPI("/api/v1/admin/workflow-triggers");
export const createWorkflowTrigger = (body) =>
  fetchAPI("/api/v1/admin/workflow-triggers", { method: "POST", body });
export const updateWorkflowTrigger = (id, body) =>
  fetchAPI(`/api/v1/admin/workflow-triggers/${id}`, { method: "PATCH", body });
export const deleteWorkflowTrigger = (id) =>
  fetchAPI(`/api/v1/admin/workflow-triggers/${id}`, { method: "DELETE" });
// Dry run: the next occurrences of a recurrence, computed by the SAME
// services.workflow_schedule functions the firing loop uses. Nothing is stored.
export const previewWorkflowSchedule = (body) =>
  fetchAPI("/api/v1/admin/workflow-triggers/preview", { method: "POST", body });
export const getWorkflowVersions = (id) =>
  fetchAPI(`/api/v1/admin/workflows/${id}/versions`);

// --- TA Model — admin settings (TA Model Sprint 2) ---
// Returns an ENVELOPE — the 4 modeling.ta.* settings, `strategy_overrides`
// (per-strategy "your override" vs. "platform default" — see
// services.ta_config.strategy_overrides) and `permissions.can_write` — same
// no-client-fallback contract as the Triggers screen. Open read (any
// authenticated org member); writes need can_manage_org_settings, enforced
// server-side.
export const getTaDefaults = () => fetchAPI("/api/v1/modeling/ta/defaults");

// --- TA Model — commitment projection UX (TA Model Sprint 3) ---
// GET returns the saved, calibrated-or-strategy-default projection for one
// real commitment (never persisted itself — computed at read time). Gated
// server-side on view_portfolio, the same real permission every other
// portfolio read endpoint uses (Task 1b) — not a new one.
export const getTaProjection = (commitmentId, { strategyKey, periodsPerYear, horizonPeriods } = {}) =>
  fetchAPI(`/api/v1/modeling/ta/projection/${commitmentId}`, {
    searchParams: {
      strategy_key: strategyKey,
      periods_per_year: periodsPerYear,
      horizon_periods: horizonPeriods,
    },
  });

// --- TA Model — obligation ledger (TA Model Sprint 4, Task 2) ---
// A real, 36-month forward capital-call visibility view, computed at read
// time from the same live projection GET above uses — never persisted.
// Gated server-side on view_portfolio, same as the projection read.
export const getTaObligationLedger = (commitmentId, { strategyKey, periodsPerYear } = {}) =>
  fetchAPI(`/api/v1/modeling/ta/obligations/${commitmentId}`, {
    searchParams: { strategy_key: strategyKey, periods_per_year: periodsPerYear },
  });

// --- TA Model — calibration (TA Model Sprint 4, Task 3) ---
// Gated server-side on manage_portfolio — a REAL, stricter gate than the
// view_portfolio reads above (Task 1b). `dryRun: true` fits and validates
// (including the real frequency-aware floor) without persisting anything —
// a genuine preview-then-confirm flow against the real endpoint.
export const postTaCalibrate = (commitmentId, { taStrategyKey, periodsPerYear, dryRun = false }) =>
  fetchAPI(`/api/v1/modeling/ta/calibrate/${commitmentId}`, {
    method: "POST",
    body: { ta_strategy_key: taStrategyKey, periods_per_year: periodsPerYear, dry_run: dryRun },
  });

// --- Entity Hierarchy (Sprint 15) ---
export const getEntityTree = (id) => fetchAPI(`/api/v1/entities/${id}/tree`);
export const getEntityLookthrough = (id) => fetchAPI(`/api/v1/entities/${id}/lookthrough`);
export const getEntityRelationships = (id) => fetchAPI(`/api/v1/entities/${id}/relationships`);
export const createEntityRelationship = (body) => fetchAPI("/api/v1/entity-relationships", { method: "POST", body });
export const updateEntityRelationship = (id, body) => fetchAPI(`/api/v1/entity-relationships/${id}`, { method: "PATCH", body });
export const deleteEntityRelationship = (id) => fetchAPI(`/api/v1/entity-relationships/${id}`, { method: "DELETE" });
export const listEntityGroups = () => fetchAPI("/api/v1/entity-groups");
export const getEntityGroup = (id) => fetchAPI(`/api/v1/entity-groups/${id}`);
export const createEntityGroup = (body) => fetchAPI("/api/v1/entity-groups", { method: "POST", body });
export const addEntityGroupMember = (groupId, entityId) => fetchAPI(`/api/v1/entity-groups/${groupId}/members`, { method: "POST", body: { entity_id: entityId } });
export const removeEntityGroupMember = (groupId, entityId) => fetchAPI(`/api/v1/entity-groups/${groupId}/members/${entityId}`, { method: "DELETE" });

// --- Note-terms review queue + STP trust policy (Super Admin, global data) ---
// No org_id in any of these: securities_global_note_terms / reference_filings /
// note_terms_stp_policy are global SEC reference data with no tenant.
export const getNoteTermsQueue = () =>
  fetchAPI("/api/v1/admin/pricing/note-terms/queue");
export const resolveNoteTermsField = (noteTermsId, body) =>
  fetchAPI(`/api/v1/admin/pricing/note-terms/${noteTermsId}/resolve`, {
    method: "POST", body,
  });
export const listStpPolicies = () =>
  fetchAPI("/api/v1/admin/pricing/stp-policy");
export const grantStpPolicy = (body) =>
  fetchAPI("/api/v1/admin/pricing/stp-policy", { method: "POST", body });
export const revokeStpPolicy = (policyId) =>
  fetchAPI(`/api/v1/admin/pricing/stp-policy/${policyId}`, { method: "DELETE" });

// --- Chancery Phase 6 (document review / confirm) ---
export const getDocumentReview = (documentId) =>
  fetchAPI(`/api/v1/documents/${documentId}/review`);
export const submitFieldCorrection = (documentId, body) =>
  fetchAPI(`/api/v1/documents/${documentId}/corrections`, { method: "POST", body });
export const confirmDocument = (documentId) =>
  fetchAPI(`/api/v1/documents/${documentId}/confirm`, { method: "POST" });
// Phase 5 linkage endpoints, reused directly by the review screen (no duplication).
export const getDocumentLinks = (documentId) =>
  fetchAPI(`/api/v1/documents/${documentId}/links`);
export const linkDocumentEntities = (documentId, entityIds, linkRole) =>
  fetchAPI(`/api/v1/documents/${documentId}/entity-links`, {
    method: "POST", body: { entity_ids: entityIds, link_role: linkRole || null },
  });
export const unlinkDocumentEntity = (documentId, entityId) =>
  fetchAPI(`/api/v1/documents/${documentId}/entity-links/${entityId}`, { method: "DELETE" });
