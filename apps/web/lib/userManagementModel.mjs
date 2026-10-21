// USERMGMT1 — People & Access list logic, pure (no React, no "@/" imports) so
// node tests exercise exactly what the screen runs.
//
// Every control on a row is decided by the SERVER: GET /admin/users publishes
// `actions` per row for THIS caller. `can()` reads only that array and fails
// closed — a row without it (an older API, a lost field) gets no controls.

export const ACTION = {
  approve: "approve",
  reject: "reject",
  link: "link",
  resetPassword: "reset_password",
  resendInvite: "resend_invite",
  editEmail: "edit_email",
  edit: "edit",
  retire: "retire",
};

// The status filter's values are the API's STATUS_FILTERS (routers/admin.py).
export const STATUS_FILTER_OPTIONS = [
  { value: "", label: "All statuses" },
  { value: "active", label: "Active" },
  { value: "pending", label: "Awaiting approval" },
  { value: "deactivated", label: "Deactivated" },
  { value: "retired", label: "Retired" },
  { value: "rejected", label: "Declined" },
];

export const PENDING_EXPLANATION =
  "A pending sign-in is someone who signed in but did not match an existing member, " +
  "so the platform is holding them until an administrator decides. Approve grants them a role. " +
  "Link attaches the sign-in to a person who already has an account here — use it when someone " +
  "signed in a different way than before. Decline keeps them out.";

export const EMAIL_EDIT_NOTE =
  "This changes the email shown here and used for invitations and password resets. " +
  "It does not change the email the person signs in with.";

export function can(user, action) {
  return Boolean(user && Array.isArray(user.actions) && user.actions.includes(action));
}

export function roleLabel(name) {
  if (!name) return "—";
  return String(name)
    .split("_")
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

export function statusLabel(user) {
  if (!user) return "—";
  if (user.is_deleted || user.status === "retired") return "Retired";
  if (user.status === "pending") return "Awaiting approval";
  if (user.status === "rejected") return "Declined";
  if (user.is_active === false) return "Deactivated";
  // An invite only describes the account until the person signs in.
  if (user.never_signed_in && user.invite_status === "pending") return "Invited";
  if (user.never_signed_in && user.invite_status === "revoked") return "Invite revoked";
  if (user.never_signed_in) return "Never signed in";
  return "Active";
}

/** One line per login: "2nd Act · Email & password", "+ Google (linked)". */
export function signInLines(user) {
  const list = Array.isArray(user?.sign_in) ? user.sign_in : [];
  if (list.length === 0) return ["Never signed in"];
  return list.map((s, i) => {
    const base = `${s.tenant} · ${s.method}`;
    if (i === 0 && s.linked_via === "primary") return base;
    return `${base} (linked)`;
  });
}

export function displayEmail(user) {
  if (!user) return "";
  if (user.email_is_placeholder) return "No email on file";
  return user.email || "";
}

/** Query params for GET /admin/users. Empty values are dropped by fetchAPI. */
export function listParams({ search, role, status, showTest, showRetired, limit = 200 } = {}) {
  return {
    search: search || undefined,
    role: role || undefined,
    status: status || undefined,
    include_test: showTest ? "true" : undefined,
    include_retired: showRetired ? "true" : undefined,
    limit,
  };
}

/** Active, real people a pending sign-in may be linked to. */
export function linkCandidates(users, pending) {
  return (users || []).filter(
    (u) =>
      u.id !== pending?.id &&
      u.status === "active" &&
      u.is_active !== false &&
      !u.is_deleted &&
      !u.is_test,
  );
}
