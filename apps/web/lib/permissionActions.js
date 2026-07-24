"use server";

// Server actions for the SOC Phase A admin screens (Profiles, Permission Sets,
// and the user-management profile selector). Each wraps a server-side API call
// (auth enforced server-side — Org Admin own org or Super Admin) and returns a
// plain {ok, ...} result the client component can render. These manage the
// additive profile-permission layer only; they do NOT change roles.

import {
  assignPermissionSetToUser,
  createPermissionSet,
  createProfile,
  deletePermissionSet,
  deleteProfile,
  getPermissionSets,
  getProfiles,
  removePermissionSetFromUser,
  setUserProfile,
  togglePermissionSetPermission,
  toggleProfilePermission,
} from "@/lib/api";

// --- Profiles ---
export async function createProfileAction(name, description) {
  try {
    const profile = await createProfile({
      name,
      description: description || null,
    });
    return { ok: true, profile };
  } catch (error) {
    return { ok: false, error: error.message };
  }
}

export async function toggleProfilePermissionAction(
  profileId,
  permissionKey,
  granted,
) {
  try {
    const res = await toggleProfilePermission(profileId, permissionKey, granted);
    return { ok: true, permissionKeys: res.permission_keys };
  } catch (error) {
    return { ok: false, error: error.message };
  }
}

export async function deleteProfileAction(profileId) {
  try {
    await deleteProfile(profileId);
    const profiles = await getProfiles();
    return { ok: true, profiles };
  } catch (error) {
    return { ok: false, error: error.message };
  }
}

// --- Permission sets ---
export async function createPermissionSetAction(name, description) {
  try {
    const set = await createPermissionSet({
      name,
      description: description || null,
    });
    return { ok: true, set };
  } catch (error) {
    return { ok: false, error: error.message };
  }
}

export async function togglePermissionSetPermissionAction(
  setId,
  permissionKey,
  granted,
) {
  try {
    const res = await togglePermissionSetPermission(
      setId,
      permissionKey,
      granted,
    );
    return { ok: true, permissionKeys: res.permission_keys };
  } catch (error) {
    return { ok: false, error: error.message };
  }
}

export async function deletePermissionSetAction(setId) {
  try {
    await deletePermissionSet(setId);
    const sets = await getPermissionSets();
    return { ok: true, sets };
  } catch (error) {
    return { ok: false, error: error.message };
  }
}

export async function assignPermissionSetAction(setId, userId) {
  try {
    await assignPermissionSetToUser(setId, userId);
    const sets = await getPermissionSets();
    return { ok: true, sets };
  } catch (error) {
    return { ok: false, error: error.message };
  }
}

export async function removePermissionSetAction(setId, userId) {
  try {
    await removePermissionSetFromUser(setId, userId);
    const sets = await getPermissionSets();
    return { ok: true, sets };
  } catch (error) {
    return { ok: false, error: error.message };
  }
}

// --- User → profile assignment (Task 3) ---
export async function setUserProfileAction(userId, profileId) {
  try {
    const res = await setUserProfile(userId, profileId || null);
    return { ok: true, profileId: res.profile_id };
  } catch (error) {
    return { ok: false, error: error.message };
  }
}
