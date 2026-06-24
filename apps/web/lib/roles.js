// Role helpers for the Auth0 session user.
//
// When the Auth0 tenant adds a namespaced `roles` claim (via a login Action),
// it appears on `session.user`. Until RBAC is configured the claim is absent;
// to avoid locking out the single admin operator we treat "no roles claim" as
// staff (dev / single-admin stage), mirroring the API's default-allow posture.

const ROLE_CLAIMS = [
  "https://2ndactcapital.com/roles",
  "https://api.2ndactcapital.com/roles",
  "roles",
];

const STAFF_ROLES = new Set([
  "investment_staff",
  "admin",
  "super_admin",
  "owner",
]);

export function getRoles(user) {
  if (!user) return null;
  for (const claim of ROLE_CLAIMS) {
    const value = user[claim];
    if (value == null) continue;
    return Array.isArray(value) ? value : [value];
  }
  return null;
}

export function isStaff(user) {
  const roles = getRoles(user);
  if (roles === null) return true; // No RBAC configured yet — default to staff.
  return roles.some((r) => STAFF_ROLES.has(r));
}
