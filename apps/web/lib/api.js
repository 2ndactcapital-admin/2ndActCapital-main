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
