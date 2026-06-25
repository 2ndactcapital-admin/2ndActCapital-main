"use server";

import { assignUserRole, getAdminUsers } from "@/lib/api";

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
