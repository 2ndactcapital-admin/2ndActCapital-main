"use client";

import { useEffect, useState } from "react";

// Module-level cache so the profile is fetched once per page load and shared
// across every component that gates on permissions.
let _cache = null;
let _inflight = null;

async function loadMe() {
  if (_cache) return _cache;
  if (!_inflight) {
    _inflight = fetch("/api/users/me", { cache: "no-store" })
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        _cache = data || { role: null, roles: [], permissions: [] };
        return _cache;
      })
      .catch(() => {
        _cache = { role: null, roles: [], permissions: [] };
        return _cache;
      })
      .finally(() => {
        _inflight = null;
      });
  }
  return _inflight;
}

/**
 * Client hook exposing the current user's role + permissions.
 * Returns { loading, role, roles, permissions, can(perm) }.
 *
 * Single-admin safety: while RBAC is unpopulated the API returns an empty
 * permissions list AND no roles; `can()` treats "no roles assigned" as
 * default-allow, mirroring the backend so the sole operator is never gated out.
 */
export function usePermissions() {
  const [me, setMe] = useState(_cache);
  const [loading, setLoading] = useState(!_cache);

  useEffect(() => {
    let active = true;
    if (!_cache) {
      loadMe().then((data) => {
        if (active) {
          setMe(data);
          setLoading(false);
        }
      });
    }
    return () => {
      active = false;
    };
  }, []);

  const permissions = me?.permissions || [];
  const roles = me?.roles || [];
  const noRolesYet = roles.length === 0;

  function can(permission) {
    // No roles assigned yet → default allow (matches backend posture).
    if (noRolesYet) return true;
    return permissions.includes(permission);
  }

  return {
    loading,
    role: me?.role || null,
    roles,
    permissions,
    can,
    navPinned: me?.nav_pinned ?? null,
  };
}
