"use client";

import { useEffect, useState } from "react";
import { canPerm } from "@/lib/menuVisibility";

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
 * Fail closed (AUTHZ1): `can()` is true only for Super Admin or a permission
 * the server's DB Principal actually grants. A failed /users/me yields an
 * empty envelope, which grants nothing.
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
  const accountRole = me?.account_role || null;

  // Delegates to the single, pure rule in lib/menuVisibility.mjs — Super Admin
  // FIRST, then the granted permission set (no default-allow since AUTHZ1).
  // See that module for why the missing super-admin bypass was a real (not
  // theoretical) lockout.
  function can(permission) {
    return canPerm(me, permission);
  }

  return {
    loading,
    // The raw /users/me payload — pass it to lib/menuVisibility's canAccess()
    // so role gates use the same rule (super-admin-first) as permission gates.
    me,
    role: me?.role || null,
    // Raw users.role — what the Sprint 24 admin gates check.
    accountRole,
    roles,
    permissions,
    can,
    navPinned: me?.nav_pinned ?? null,
  };
}
