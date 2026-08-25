"use server";

import { createInvite, getInvites, revokeInvite } from "@/lib/api";

/**
 * Server actions for admin member provisioning.
 *
 * WHY THIS FILE IS NEW. The invite backend (POST/GET/POST-revoke
 * /api/v1/admin/invites, plus services/invites.py and the users.invite_* columns)
 * shipped in Multi-tenant Sprint 2 and works — but nothing in apps/web ever
 * called it. There was no Next.js route, no server action, no button, and no
 * /enroll page. That is why "create a user via /admin/users" fails: the screen
 * has never had a create path of any kind, only search / filter / edit-role.
 *
 * org_id is NEVER passed from here. The backend resolves it from the caller's
 * own token context (get_org_id), so an admin can only ever provision into
 * their own org — the standing multi-tenant rule. These actions exist to reach
 * the endpoint, not to widen it.
 */

export async function createInviteAction({ email, fullName, role, profileId }) {
  try {
    const invite = await createInvite({ email, fullName, role, profileId });
    return { ok: true, invite };
  } catch (error) {
    return { ok: false, error: error.message };
  }
}

export async function listInvitesAction(status) {
  try {
    const invites = await getInvites(status);
    return { ok: true, invites };
  } catch (error) {
    return { ok: false, error: error.message };
  }
}

export async function revokeInviteAction(inviteId) {
  try {
    const invite = await revokeInvite(inviteId);
    return { ok: true, invite };
  } catch (error) {
    return { ok: false, error: error.message };
  }
}
