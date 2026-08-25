"use client";

import { useMemo, useState, useTransition } from "react";
import {
  assignRoleAction,
  deactivateUserAction,
  deleteUserAction,
  reactivateUserAction,
  searchUsersAction,
  updateUserAction,
} from "@/lib/adminActions";
import { setUserProfileAction } from "@/lib/permissionActions";
import { createInviteAction, revokeInviteAction } from "@/lib/inviteActions";

// The two account roles POST /admin/invites accepts (services.invites
// ALLOWED_INVITE_ROLES). 'super_admin' is deliberately NOT invitable — platform
// staff come from the Hollisworks Auth0 tenant, not from an org admin's invite.
const INVITE_ROLES = [
  { value: "member", label: "Member" },
  { value: "org_admin", label: "Organization Admin" },
];

function roleLabel(name) {
  if (!name) return "—";
  return name
    .split("_")
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

function statusLabel(user) {
  // Most specific state first. An anonymized row is also inactive and may also
  // still carry an invite status, so checking in the other order would report
  // the wrong thing.
  if (user.is_deleted) return "Deleted";
  if (user.is_active === false) return "Deactivated";
  // invite_status is NULL for an ordinary enrolled account.
  if (!user.invite_status) return "Active";
  return roleLabel(user.invite_status);
}

// Absolute date + a coarse relative hint. `last_login_at` is stamped by
// services.users.touch_last_login on the first authenticated request in each
// 5-minute window, so it is "last seen" to within that window — precise to the
// second would overstate it.
function lastLoginLabel(value) {
  if (!value) return "Never";
  const when = new Date(value);
  if (Number.isNaN(when.getTime())) return "—";
  const days = Math.floor((Date.now() - when.getTime()) / 86400000);
  const date = when.toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
  if (days <= 0) return `${date} (today)`;
  if (days === 1) return `${date} (yesterday)`;
  return `${date} (${days}d ago)`;
}

function InviteModal({ profiles, onClose, onInvited }) {
  const [email, setEmail] = useState("");
  const [fullName, setFullName] = useState("");
  const [role, setRole] = useState("member");
  // Optional, additive permission persona granted at invite time so the account
  // carries it from creation rather than needing a second admin action after
  // the member enrols. Does NOT replace the role above.
  const [profileId, setProfileId] = useState("");
  const [error, setError] = useState(null);
  const [invite, setInvite] = useState(null);
  const [pending, startTransition] = useTransition();

  function submit() {
    const trimmed = email.trim();
    if (!trimmed || !trimmed.includes("@")) {
      setError("Enter a valid email address.");
      return;
    }
    setError(null);
    startTransition(async () => {
      const res = await createInviteAction({
        email: trimmed,
        fullName: fullName.trim() || null,
        role,
        profileId: profileId || null,
      });
      if (!res.ok) {
        setError(res.error || "Could not create the invitation.");
        return;
      }
      setInvite(res.invite);
      onInvited(res.invite);
    });
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-navy/30 p-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-md rounded-lg border bg-bg-card p-6"
        style={{ borderColor: "#ece8dd", boxShadow: "0 1px 3px rgba(0,0,0,0.06)" }}
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="text-base font-semibold text-navy">Invite a Member</h3>

        {invite ? (
          <>
            <p className="mt-3 text-sm text-text-secondary">
              {invite.email} has been added as a pending member. Email delivery
              is not yet enabled, so share this enrollment link directly.
            </p>
            <div className="mt-3 rounded-md border border-border bg-bg-app p-3">
              <p className="break-all font-mono text-xs text-text-primary">
                {invite.enrollment_url}
              </p>
            </div>
            <div className="mt-5 flex justify-end">
              <button
                type="button"
                onClick={onClose}
                className="rounded-md bg-navy px-4 py-2 text-sm font-medium text-bg-app transition-opacity hover:opacity-90"
              >
                Done
              </button>
            </div>
          </>
        ) : (
          <>
            <p className="mt-1 text-sm text-text-muted">
              Creates a pending account in your organization.
            </p>

            <label className="mt-4 block text-xs font-medium uppercase tracking-wide text-text-muted">
              Email
            </label>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="name@example.com"
              className="mt-1 w-full rounded-md border border-border bg-bg-card px-3 py-2 text-sm text-text-primary outline-none focus:ring-2 focus:ring-navy"
            />

            <label className="mt-4 block text-xs font-medium uppercase tracking-wide text-text-muted">
              Full Name
            </label>
            <input
              type="text"
              value={fullName}
              onChange={(e) => setFullName(e.target.value)}
              placeholder="Optional"
              className="mt-1 w-full rounded-md border border-border bg-bg-card px-3 py-2 text-sm text-text-primary outline-none focus:ring-2 focus:ring-navy"
            />

            <label className="mt-4 block text-xs font-medium uppercase tracking-wide text-text-muted">
              Role
            </label>
            <select
              value={role}
              onChange={(e) => setRole(e.target.value)}
              className="mt-1 w-full rounded-md border border-border bg-bg-card px-3 py-2 text-sm text-text-primary outline-none focus:ring-2 focus:ring-navy"
            >
              {INVITE_ROLES.map((r) => (
                <option key={r.value} value={r.value}>
                  {r.label}
                </option>
              ))}
            </select>
            <label className="mt-4 block text-xs font-medium uppercase tracking-wide text-text-muted">
              Profile
            </label>
            <select
              value={profileId}
              onChange={(e) => setProfileId(e.target.value)}
              className="mt-1 w-full rounded-md border border-border bg-bg-card px-3 py-2 text-sm text-text-primary outline-none focus:ring-2 focus:ring-navy"
            >
              <option value="">No profile</option>
              {profiles.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
            <p className="mt-1 text-xs text-text-muted">
              Optional. An additive permission persona, separate from the role
              above — granted from the moment the account is created.
            </p>

            <p className="mt-3 text-xs text-text-muted">
              The member joins your organization. Organization is taken from
              your own account and cannot be chosen here.
            </p>

            {error && <p className="mt-2 text-sm text-[#9B2335]">{error}</p>}

            <div className="mt-5 flex justify-end gap-2">
              <button
                type="button"
                onClick={onClose}
                className="rounded-md border border-border px-4 py-2 text-sm font-medium text-text-secondary hover:bg-border"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={submit}
                disabled={pending}
                className="rounded-md bg-navy px-4 py-2 text-sm font-medium text-bg-app transition-opacity hover:opacity-90 disabled:opacity-60"
              >
                {pending ? "Inviting…" : "Send Invitation"}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function EditRoleModal({ user, roles, profiles, onClose, onSaved, onSavedAndClose }) {
  const [roleId, setRoleId] = useState(user.role_id || "");
  // SOC Phase A: the profile is a NEW, separate, additive field — independent
  // of the account role above. "" means no profile assigned.
  const [profileId, setProfileId] = useState(user.profile_id || "");
  // User-management sprint: the first admin-editable users-row field. Before
  // this there was no edit endpoint for the row at all — only role and profile
  // assignment, which write other tables.
  const [fullName, setFullName] = useState(user.full_name || "");
  const [error, setError] = useState(null);
  // Two-step confirm for the destructive action. No window.confirm(): the
  // standing rule is no interactive prompts, and a native dialog cannot explain
  // that "delete" anonymizes rather than removes.
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [notice, setNotice] = useState(null);
  const [pending, startTransition] = useTransition();

  const deleted = !!user.is_deleted;
  const active = user.is_active !== false;

  function runLifecycle(action, describe) {
    setError(null);
    setNotice(null);
    startTransition(async () => {
      const res = await action();
      if (!res.ok) {
        setError(res.error || "Could not update the account.");
        return;
      }
      setNotice(describe(res.user));
      onSaved({
        ...user,
        full_name: res.user.full_name,
        email: res.user.email,
        is_active: res.user.is_active,
        deactivated_at: res.user.deactivated_at,
        is_deleted: !!res.user.anonymized || user.is_deleted,
        // An anonymized account keeps no grants (the backend deletes its
        // user_roles / user_permission_sets rows), so the row must not keep
        // rendering the ones it used to hold.
        ...(res.user.anonymized
          ? { role: null, role_id: null, profile_id: null, profile_name: null }
          : {}),
      });
    });
  }

  function save() {
    if (!roleId) {
      setError("Select a role.");
      return;
    }
    const trimmedName = fullName.trim();
    if (!trimmedName) {
      setError("Name cannot be empty.");
      return;
    }
    setError(null);
    startTransition(async () => {
      let updated = { ...user };

      // Persist a name change only when it actually changed.
      if (trimmedName !== (user.full_name || "")) {
        const res = await updateUserAction(user.id, { fullName: trimmedName });
        if (!res.ok) {
          setError(res.error || "Could not update the name.");
          return;
        }
        updated = { ...updated, full_name: res.user.full_name };
      }

      // Persist a role change only when it actually changed (role logic
      // unchanged from the original screen).
      if (roleId !== (user.role_id || "")) {
        const res = await assignRoleAction(user.id, roleId);
        if (!res.ok) {
          setError(res.error || "Could not update role.");
          return;
        }
        updated = { ...updated, role: res.user.role, role_id: res.user.role_id };
      }

      // Persist a profile change independently.
      if (profileId !== (user.profile_id || "")) {
        const res = await setUserProfileAction(user.id, profileId || null);
        if (!res.ok) {
          setError(res.error || "Could not update profile.");
          return;
        }
        const match = profiles.find((p) => p.id === profileId);
        updated = {
          ...updated,
          profile_id: profileId || null,
          profile_name: match ? match.name : null,
        };
      }

      onSavedAndClose(updated);
    });
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-navy/30 p-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-md rounded-lg border bg-bg-card p-6"
        style={{ borderColor: "#ece8dd", boxShadow: "0 1px 3px rgba(0,0,0,0.06)" }}
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="text-base font-semibold text-navy">Edit Member</h3>
        <div className="mt-3 rounded-md border border-border bg-bg-app p-3 text-sm">
          <p className="text-text-muted">{user.email}</p>
          <p className="mt-1 text-xs text-text-muted">
            Last sign-in: {lastLoginLabel(user.last_login_at)}
          </p>
          <p className="mt-1 text-xs text-text-muted">
            Status: {statusLabel(user)}
          </p>
        </div>

        <label className="mt-4 block text-xs font-medium uppercase tracking-wide text-text-muted">
          Name
        </label>
        <input
          type="text"
          value={fullName}
          disabled={deleted}
          onChange={(e) => setFullName(e.target.value)}
          placeholder="Full name"
          className="mt-1 w-full rounded-md border border-border bg-bg-card px-3 py-2 text-sm text-text-primary outline-none focus:ring-2 focus:ring-navy disabled:opacity-60"
        />

        <label className="mt-4 block text-xs font-medium uppercase tracking-wide text-text-muted">
          Role
        </label>
        <select
          value={roleId}
          onChange={(e) => setRoleId(e.target.value)}
          className="mt-1 w-full rounded-md border border-border bg-bg-card px-3 py-2 text-sm text-text-primary outline-none focus:ring-2 focus:ring-navy"
        >
          <option value="">Select a role…</option>
          {roles.map((r) => (
            <option key={r.id} value={r.id}>
              {roleLabel(r.name)}
            </option>
          ))}
        </select>

        <label className="mt-4 block text-xs font-medium uppercase tracking-wide text-text-muted">
          Profile
        </label>
        <select
          value={profileId}
          onChange={(e) => setProfileId(e.target.value)}
          className="mt-1 w-full rounded-md border border-border bg-bg-card px-3 py-2 text-sm text-text-primary outline-none focus:ring-2 focus:ring-navy"
        >
          <option value="">No profile</option>
          {profiles.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
        <p className="mt-1 text-xs text-text-muted">
          Additive permission persona — separate from the account role.
        </p>

        {/* ── Account lifecycle ───────────────────────────────────────── */}
        <div className="mt-6 border-t border-border pt-4">
          <p
            className="text-xs font-semibold uppercase"
            style={{ letterSpacing: "0.22em", color: "var(--2a-gold)" }}
          >
            Account
          </p>

          {deleted ? (
            <p className="mt-2 text-sm text-text-muted">
              This account has been anonymized. Its identifying details were
              cleared and its sign-in was severed; the row itself is retained so
              the audit trail and every record it created still resolve.
            </p>
          ) : (
            <>
              <p className="mt-2 text-sm text-text-secondary">
                {active
                  ? "Deactivating takes effect immediately — the member's next request is refused, even on a session they already hold."
                  : "This account is deactivated. The member cannot sign in or use an existing session."}
              </p>

              <div className="mt-3 flex flex-wrap gap-2">
                {active ? (
                  <button
                    type="button"
                    disabled={pending}
                    onClick={() =>
                      runLifecycle(
                        () => deactivateUserAction(user.id),
                        () => "Account deactivated.",
                      )
                    }
                    className="rounded-md border px-3 py-1.5 text-sm font-medium disabled:opacity-60"
                    style={{ borderColor: "#9B2335", color: "#9B2335" }}
                  >
                    Deactivate
                  </button>
                ) : (
                  <button
                    type="button"
                    disabled={pending}
                    onClick={() =>
                      runLifecycle(
                        () => reactivateUserAction(user.id),
                        () => "Account reactivated.",
                      )
                    }
                    className="rounded-md border px-3 py-1.5 text-sm font-medium disabled:opacity-60"
                    style={{ borderColor: "#2D6A4F", color: "#2D6A4F" }}
                  >
                    Reactivate
                  </button>
                )}

                {confirmingDelete ? (
                  <>
                    <button
                      type="button"
                      disabled={pending}
                      onClick={() =>
                        runLifecycle(
                          () => deleteUserAction(user.id),
                          (u) =>
                            u.anonymized
                              ? "Account anonymized. The row was retained so existing records still resolve."
                              : "Account removed.",
                        )
                      }
                      className="rounded-md px-3 py-1.5 text-sm font-medium text-bg-app disabled:opacity-60"
                      style={{ background: "#9B2335" }}
                    >
                      {pending ? "Working…" : "Confirm — anonymize"}
                    </button>
                    <button
                      type="button"
                      onClick={() => setConfirmingDelete(false)}
                      className="rounded-md border border-border px-3 py-1.5 text-sm font-medium text-text-secondary hover:bg-border"
                    >
                      Keep account
                    </button>
                  </>
                ) : (
                  <button
                    type="button"
                    disabled={pending}
                    onClick={() => setConfirmingDelete(true)}
                    className="rounded-md border px-3 py-1.5 text-sm font-medium disabled:opacity-60"
                    style={{ borderColor: "#9B2335", color: "#9B2335" }}
                  >
                    Delete
                  </button>
                )}
              </div>

              {confirmingDelete && (
                // Stated plainly, because the button says "Delete" and the
                // backend does something else — and the difference matters to
                // whoever clicks it.
                <p className="mt-3 text-sm text-text-secondary">
                  Deleting <strong>anonymizes</strong> this account rather than
                  removing it. Their name and email are replaced, their sign-in
                  is severed and every role and permission grant is revoked — but
                  the row is kept, because deals, documents, entities and the
                  audit log reference it. This cannot be undone.
                </p>
              )}
            </>
          )}
        </div>

        {error && <p className="mt-3 text-sm text-[#9B2335]">{error}</p>}
        {notice && <p className="mt-3 text-sm text-[#2D6A4F]">{notice}</p>}

        <div className="mt-5 flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            className="rounded-md border border-border px-4 py-2 text-sm font-medium text-text-secondary hover:bg-border"
          >
            {deleted ? "Close" : "Cancel"}
          </button>
          {!deleted && (
            <button
              type="button"
              onClick={save}
              disabled={pending}
              className="rounded-md bg-navy px-4 py-2 text-sm font-medium text-bg-app transition-opacity hover:opacity-90 disabled:opacity-60"
            >
              {pending ? "Saving…" : "Save"}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

export default function UserManagement({
  initialUsers = [],
  roles = [],
  profiles = [],
}) {
  const [users, setUsers] = useState(initialUsers);
  const [search, setSearch] = useState("");
  const [roleFilter, setRoleFilter] = useState("");
  const [editing, setEditing] = useState(null);
  const [inviting, setInviting] = useState(false);
  const [pending, startTransition] = useTransition();

  const filtered = useMemo(() => {
    return users.filter((u) => {
      if (roleFilter && u.role !== roleFilter) return false;
      if (search) {
        const q = search.toLowerCase();
        const hay = `${u.full_name || ""} ${u.email || ""}`.toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
  }, [users, search, roleFilter]);

  function refetch(nextSearch, nextRole) {
    startTransition(async () => {
      const res = await searchUsersAction({
        search: nextSearch || undefined,
        role: nextRole || undefined,
      });
      if (res.ok) setUsers(res.users || []);
    });
  }

  function onInvited(invite) {
    // Show the new pending account immediately. The shape matches what
    // GET /admin/users returns for the same row (invite_status 'pending',
    // account_role from users.role, no granted user_roles row yet).
    setUsers((prev) => [
      {
        id: invite.id,
        email: invite.email,
        full_name: invite.full_name,
        role: null,
        role_id: null,
        // The invite may carry a profile now; reflect what was actually granted
        // rather than assuming none.
        profile_id: invite.profile_id || null,
        profile_name:
          profiles.find((p) => p.id === invite.profile_id)?.name || null,
        invite_status: invite.invite_status,
        account_role: invite.role,
        is_active: true,
        last_login_at: null,
        is_deleted: false,
      },
      ...prev,
    ]);
  }

  function onRevoke(user) {
    startTransition(async () => {
      const res = await revokeInviteAction(user.id);
      if (!res.ok) return;
      setUsers((prev) =>
        prev.map((u) =>
          u.id === user.id ? { ...u, invite_status: "revoked" } : u,
        ),
      );
    });
  }

  function onSaved(updated) {
    setUsers((prev) =>
      prev.map((u) => (u.id === updated.id ? { ...u, ...updated } : u)),
    );
    // A lifecycle action reports its result INSIDE the modal, so keep it open
    // and refresh it from the merged row; only an explicit Save closes it.
    setEditing((cur) =>
      cur && cur.id === updated.id ? { ...cur, ...updated } : cur,
    );
  }

  function onSavedAndClose(updated) {
    onSaved(updated);
    setEditing(null);
  }

  return (
    <div className="mt-6">
      <div className="flex flex-wrap items-center gap-3">
        <input
          type="search"
          placeholder="Search name or email…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          onBlur={() => refetch(search, roleFilter)}
          className="w-64 rounded-md border border-border bg-bg-card px-3 py-2 text-sm text-text-primary outline-none focus:ring-2 focus:ring-navy"
        />
        <select
          value={roleFilter}
          onChange={(e) => {
            setRoleFilter(e.target.value);
            refetch(search, e.target.value);
          }}
          className="rounded-md border border-border bg-bg-card px-3 py-2 text-sm text-text-primary outline-none focus:ring-2 focus:ring-navy"
        >
          <option value="">All roles</option>
          {roles.map((r) => (
            <option key={r.id} value={r.name}>
              {roleLabel(r.name)}
            </option>
          ))}
        </select>
        {pending && <span className="text-xs text-text-muted">Loading…</span>}
        <button
          type="button"
          onClick={() => setInviting(true)}
          className="ml-auto rounded-md bg-navy px-4 py-2 text-sm font-medium text-bg-app transition-opacity hover:opacity-90"
        >
          Invite Member
        </button>
      </div>

      <div
        className="mt-4 overflow-hidden rounded-lg border bg-bg-card"
        style={{ borderColor: "#ece8dd" }}
      >
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-text-muted">
              <th className="px-4 py-3 font-medium">Name</th>
              <th className="px-4 py-3 font-medium">Email</th>
              <th className="px-4 py-3 font-medium">Role</th>
              <th className="px-4 py-3 font-medium">Profile</th>
              <th className="px-4 py-3 font-medium">Last Sign-in</th>
              <th className="px-4 py-3 font-medium">Status</th>
              <th className="px-4 py-3 text-right font-medium">Actions</th>
            </tr>
          </thead>
          <tbody>
            {filtered.length === 0 ? (
              <tr>
                <td
                  colSpan={7}
                  className="px-4 py-10 text-center text-text-muted"
                >
                  No members found.
                </td>
              </tr>
            ) : (
              filtered.map((u) => (
                <tr
                  key={u.id}
                  className="border-b border-border last:border-b-0"
                >
                  <td className="px-4 py-3 font-medium text-text-primary">
                    {u.full_name || "—"}
                  </td>
                  <td className="px-4 py-3 text-text-secondary">{u.email}</td>
                  <td className="px-4 py-3">
                    <span className="inline-flex items-center rounded-full bg-gold-light px-2.5 py-0.5 text-xs font-medium text-navy">
                      {roleLabel(u.role)}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-text-secondary">
                    {u.profile_name || "—"}
                  </td>
                  <td className="px-4 py-3 text-text-secondary">
                    {lastLoginLabel(u.last_login_at)}
                  </td>
                  <td
                    className="px-4 py-3"
                    style={{
                      color:
                        u.is_deleted || u.is_active === false
                          ? "#9B2335"
                          : "var(--2a-text)",
                    }}
                  >
                    {statusLabel(u)}
                  </td>
                  <td className="px-4 py-3 text-right">
                    {u.invite_status === "pending" && (
                      <button
                        type="button"
                        onClick={() => onRevoke(u)}
                        className="mr-4 text-sm font-medium text-[#9B2335] hover:underline"
                      >
                        Revoke
                      </button>
                    )}
                    <button
                      type="button"
                      onClick={() => setEditing(u)}
                      className="text-sm font-medium text-navy hover:underline"
                    >
                      Edit
                    </button>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {inviting && (
        <InviteModal
          profiles={profiles}
          onClose={() => setInviting(false)}
          onInvited={onInvited}
        />
      )}

      {editing && (
        <EditRoleModal
          user={editing}
          roles={roles}
          profiles={profiles}
          onClose={() => setEditing(null)}
          onSaved={onSaved}
          onSavedAndClose={onSavedAndClose}
        />
      )}
    </div>
  );
}
