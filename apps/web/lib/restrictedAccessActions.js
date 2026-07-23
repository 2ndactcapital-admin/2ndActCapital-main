"use server";

// Server actions for the SOC Phase 4 restricted-access admin screen. Each wraps
// a server-side API call (Super Admin enforced server-side) and returns a plain
// {ok, ...} result the client component can render. These only populate the
// restriction/allow-list data the unified filter reads — no visibility
// ENFORCEMENT change this phase.

import {
  getRestrictedAccounts,
  grantRestrictedAccess,
  revokeRestrictedAccess,
  setEntityRestricted,
} from "@/lib/api";

export async function setRestrictedAction(entityId, restricted, notes) {
  try {
    await setEntityRestricted(entityId, { restricted, notes: notes || null });
    const data = await getRestrictedAccounts();
    return { ok: true, restricted: data.restricted || [] };
  } catch (error) {
    return { ok: false, error: error.message };
  }
}

export async function grantAccessAction(entityId, userId, reason) {
  try {
    await grantRestrictedAccess(entityId, {
      user_id: userId,
      reason: reason || null,
    });
    const data = await getRestrictedAccounts();
    return { ok: true, restricted: data.restricted || [] };
  } catch (error) {
    return { ok: false, error: error.message };
  }
}

export async function revokeAccessAction(entityId, userId) {
  try {
    await revokeRestrictedAccess(entityId, userId);
    const data = await getRestrictedAccounts();
    return { ok: true, restricted: data.restricted || [] };
  } catch (error) {
    return { ok: false, error: error.message };
  }
}
