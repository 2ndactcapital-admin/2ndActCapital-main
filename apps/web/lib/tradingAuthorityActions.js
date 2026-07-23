"use server";

// Server actions for the SOC Phase 5 trading-authority admin screen. Each wraps
// a server-side API call (Super Admin enforced server-side) and returns a plain
// {ok, ...} result the client component can render. These only populate the
// grant data the enforcement engine (services.trading_authority) reads — no
// money-movement enforcement change is wired this phase.

import {
  getTradingAuthorityGrants,
  revokeTradingAuthorityGrant,
  upsertTradingAuthorityGrant,
} from "@/lib/api";

export async function upsertGrantAction(entityId, userId, authorityTier) {
  try {
    await upsertTradingAuthorityGrant({
      entity_id: entityId,
      user_id: userId,
      authority_tier: authorityTier,
    });
    const data = await getTradingAuthorityGrants();
    return { ok: true, grants: data.grants || [] };
  } catch (error) {
    return { ok: false, error: error.message };
  }
}

export async function revokeGrantAction(entityId, userId) {
  try {
    await revokeTradingAuthorityGrant(entityId, userId);
    const data = await getTradingAuthorityGrants();
    return { ok: true, grants: data.grants || [] };
  } catch (error) {
    return { ok: false, error: error.message };
  }
}
