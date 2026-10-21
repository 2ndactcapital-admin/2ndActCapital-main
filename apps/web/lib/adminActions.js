"use server";

import {
  approvePendingUser,
  assignUserRole,
  deactivateUser,
  deleteUser,
  getAdminUsers,
  linkPendingUser,
  reactivateUser,
  rejectPendingUser,
  resendUserInvite,
  sendPasswordReset,
  updateAdminUser,
  updateUserEmail,
} from "@/lib/api";

// Assign a role to a user (admin only — enforced server-side by manage_members).
export async function assignRoleAction(userId, roleId) {
  try {
    const user = await assignUserRole(userId, roleId);
    return { ok: true, user };
  } catch (error) {
    return { ok: false, error: error.message };
  }
}

// Re-query the user list (used after search / filter changes).
export async function searchUsersAction(searchParams) {
  try {
    const users = await getAdminUsers(searchParams);
    return { ok: true, users };
  } catch (error) {
    return { ok: false, error: error.message };
  }
}

// --- Account lifecycle (user-management sprint) ---
// org_id is never passed from here: the backend takes it from the caller's own
// token context and 404s a target outside it. These actions exist to reach the
// endpoints, not to widen them.

// Rename a member. Only full_name is editable — see the backend's
// UserUpdateRequest, which rejects any other field outright.
export async function updateUserAction(userId, { fullName }) {
  try {
    const user = await updateAdminUser(userId, { fullName });
    return { ok: true, user };
  } catch (error) {
    return { ok: false, error: error.message };
  }
}

export async function deactivateUserAction(userId) {
  try {
    const user = await deactivateUser(userId);
    return { ok: true, user };
  } catch (error) {
    return { ok: false, error: error.message };
  }
}

export async function reactivateUserAction(userId) {
  try {
    const user = await reactivateUser(userId);
    return { ok: true, user };
  } catch (error) {
    return { ok: false, error: error.message };
  }
}

// Anonymize, not remove — the response says which (`anonymized`,
// `hard_deleted`) and the UI reports that verbatim rather than claiming a
// deletion that did not happen.
export async function deleteUserAction(userId) {
  try {
    const user = await deleteUser(userId);
    return { ok: true, user };
  } catch (error) {
    return { ok: false, error: error.message };
  }
}

// --- Pending sign-ins (AUTHZ1) — gated server-side by manage_members or
// manage_org_settings; the screen shows the controls only when the user list
// itself loaded (the same endpoint gate).
export async function approvePendingUserAction(userId, roleId) {
  try {
    const user = await approvePendingUser(userId, roleId);
    return { ok: true, user };
  } catch (error) {
    return { ok: false, error: error.message };
  }
}

export async function rejectPendingUserAction(userId) {
  try {
    const user = await rejectPendingUser(userId);
    return { ok: true, user };
  } catch (error) {
    return { ok: false, error: error.message };
  }
}

// --- People & Access actions (USERMGMT1) ---
async function wrap(call) {
  try {
    return { ok: true, result: await call() };
  } catch (error) {
    return { ok: false, error: error.message, status: error.status };
  }
}

export async function sendPasswordResetAction(userId) {
  return wrap(() => sendPasswordReset(userId));
}

export async function resendInviteAction(userId) {
  return wrap(() => resendUserInvite(userId));
}

export async function linkPendingUserAction(userId, targetUserId) {
  return wrap(() => linkPendingUser(userId, targetUserId));
}

export async function updateUserEmailAction(userId, email) {
  return wrap(() => updateUserEmail(userId, email));
}
