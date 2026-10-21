"use client";

import { useMemo, useState, useTransition } from "react";
import {
  approvePendingUserAction,
  assignRoleAction,
  deactivateUserAction,
  deleteUserAction,
  linkPendingUserAction,
  reactivateUserAction,
  rejectPendingUserAction,
  resendInviteAction,
  searchUsersAction,
  sendPasswordResetAction,
  updateUserAction,
  updateUserEmailAction,
} from "@/lib/adminActions";
import { setUserProfileAction } from "@/lib/permissionActions";
import { createInviteAction, revokeInviteAction } from "@/lib/inviteActions";
import {
  ACTION,
  STATUS_FILTER_OPTIONS,
  can,
  displayEmail,
  listParams,
  roleLabel,
  statusLabel,
} from "@/lib/userManagementModel.mjs";
import { PendingPanel, RoleChips, SignInActions, UserRow } from "@/components/admin/PeopleParts";

// USERMGMT1 — one row per person. GET /admin/users returns each person once,
// with every role, how they sign in, and the actions THIS caller may take on
// that row (`actions`); every control below renders only when the server
// named it. Test accounts and retired accounts are hidden unless asked for.

// The two account roles POST /admin/invites accepts (services.invites
// ALLOWED_INVITE_ROLES). 'super_admin' is deliberately NOT invitable — platform
// staff come from the Hollisworks Auth0 tenant, not from an org admin's invite.
const INVITE_ROLES = [
  { value: "member", label: "Member" },
  { value: "org_admin", label: "Organization Admin" },
];

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
              {invite.email} has been invited.{" "}
              {invite.email_delivery?.sent
                ? "The invitation was emailed; the link is also below."
                : "Email delivery is not available, so share this enrollment link directly."}
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

function ManageModal({ user, roles, profiles, onClose, onSaved, onSavedAndClose }) {
  const currentRoles = Array.isArray(user.roles) ? user.roles : [];
  const [roleId, setRoleId] = useState(user.role_id || "");
  // SOC Phase A: the profile is separate from, and additive to, the role.
  const [profileId, setProfileId] = useState(user.profile_id || "");
  const [fullName, setFullName] = useState(user.full_name || "");
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);
  // Two-step confirm for Retire. No window.confirm(): a native dialog cannot
  // explain that retiring keeps the row.
  const [confirmingRetire, setConfirmingRetire] = useState(false);
  const [signInResult, setSignInResult] = useState(null);
  const [signInError, setSignInError] = useState(null);
  const [pending, startTransition] = useTransition();

  const retired = !!user.is_deleted || user.status === "retired";
  const active = user.is_active !== false;
  const editable = can(user, ACTION.edit);

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
        ...(res.user.anonymized
          ? { status: "retired", actions: [], role: null, role_id: null, roles: [], profile_id: null, profile_name: null }
          : {}),
      });
    });
  }

  function runSignIn(call, after) {
    setSignInError(null);
    setSignInResult(null);
    startTransition(async () => {
      const res = await call();
      if (!res.ok) {
        setSignInError(res.error || "The request was refused.");
        return;
      }
      setSignInResult(res.result);
      if (after) after(res.result);
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
      if (trimmedName !== (user.full_name || "")) {
        const res = await updateUserAction(user.id, { fullName: trimmedName });
        if (!res.ok) {
          setError(res.error || "Could not update the name.");
          return;
        }
        updated = { ...updated, full_name: res.user.full_name };
      }
      // PUT /admin/users/{id}/role replaces the person's roles with this one,
      // so it is sent only when the selection actually changed.
      if (roleId !== (user.role_id || "")) {
        const res = await assignRoleAction(user.id, roleId);
        if (!res.ok) {
          setError(res.error || "Could not update role.");
          return;
        }
        updated = {
          ...updated,
          role: res.user.role,
          role_id: res.user.role_id,
          roles: [{ id: res.user.role_id, name: res.user.role }],
        };
      }
      if (profileId !== (user.profile_id || "")) {
        const res = await setUserProfileAction(user.id, profileId || null);
        if (!res.ok) {
          setError(res.error || "Could not update profile.");
          return;
        }
        const match = profiles.find((p) => p.id === profileId);
        updated = { ...updated, profile_id: profileId || null, profile_name: match ? match.name : null };
      }
      onSavedAndClose(updated);
    });
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-navy/30 p-4" onClick={onClose}>
      <div
        className="max-h-[90vh] w-full max-w-lg overflow-y-auto rounded-lg border bg-bg-card p-6"
        style={{ borderColor: "#ece8dd", boxShadow: "0 1px 3px rgba(0,0,0,0.06)" }}
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="text-base font-semibold text-navy">{user.full_name || "Member"}</h3>
        <div className="mt-3 rounded-md border border-border bg-bg-app p-3 text-sm">
          <p className="text-text-muted">{displayEmail(user)}</p>
          {user.contact_email ? (
            <p className="mt-1 text-xs text-text-muted">Gave {user.contact_email} (unverified)</p>
          ) : null}
          <p className="mt-1 text-xs text-text-muted">Status: {statusLabel(user)}</p>
          <div className="mt-2"><RoleChips user={user} /></div>
        </div>

        {editable && !retired ? (
          <>
            <label className="mt-4 block text-xs font-medium uppercase tracking-wide text-text-muted">Name</label>
            <input
              type="text"
              value={fullName}
              onChange={(e) => setFullName(e.target.value)}
              placeholder="Full name"
              className="mt-1 w-full rounded-md border border-border bg-bg-card px-3 py-2 text-sm text-text-primary outline-none focus:ring-2 focus:ring-navy"
            />

            <label className="mt-4 block text-xs font-medium uppercase tracking-wide text-text-muted">Role</label>
            <select
              value={roleId}
              onChange={(e) => setRoleId(e.target.value)}
              className="mt-1 w-full rounded-md border border-border bg-bg-card px-3 py-2 text-sm text-text-primary outline-none focus:ring-2 focus:ring-navy"
            >
              <option value="">Select a role…</option>
              {roles.map((r) => (
                <option key={r.id} value={r.id}>{roleLabel(r.name)}</option>
              ))}
            </select>
            {currentRoles.length > 1 ? (
              <p className="mt-1 text-xs text-text-muted">
                This person holds {currentRoles.length} roles. Choosing a different role replaces all of them.
              </p>
            ) : null}

            <label className="mt-4 block text-xs font-medium uppercase tracking-wide text-text-muted">Profile</label>
            <select
              value={profileId}
              onChange={(e) => setProfileId(e.target.value)}
              className="mt-1 w-full rounded-md border border-border bg-bg-card px-3 py-2 text-sm text-text-primary outline-none focus:ring-2 focus:ring-navy"
            >
              <option value="">No profile</option>
              {profiles.map((p) => (
                <option key={p.id} value={p.id}>{p.name}</option>
              ))}
            </select>
          </>
        ) : null}

        <SignInActions
          user={user}
          busy={pending}
          result={signInResult}
          error={signInError}
          onReset={() => runSignIn(() => sendPasswordResetAction(user.id))}
          onResend={() =>
            runSignIn(
              () => resendInviteAction(user.id),
              (r) => onSaved({ ...user, invite_status: r.invite_status, invite_expires_at: r.invite_expires_at }),
            )
          }
          onSaveEmail={(email) =>
            runSignIn(
              () => updateUserEmailAction(user.id, email),
              (r) => onSaved({ ...user, email: r.email, email_is_placeholder: false }),
            )
          }
        />

        <div className="mt-6 border-t border-border pt-4">
          <p className="text-xs font-semibold uppercase" style={{ letterSpacing: "0.22em", color: "var(--2a-gold)" }}>
            Account
          </p>
          {retired ? (
            <p className="mt-2 text-sm text-text-muted">
              This account is retired. Its sign-in no longer works; the row is kept so the audit trail and
              every record it created still resolve.
            </p>
          ) : editable ? (
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
                    onClick={() => runLifecycle(() => deactivateUserAction(user.id), () => "Account deactivated.")}
                    className="rounded-md border px-3 py-1.5 text-sm font-medium disabled:opacity-60"
                    style={{ borderColor: "#9B2335", color: "#9B2335" }}
                  >
                    Deactivate
                  </button>
                ) : (
                  <button
                    type="button"
                    disabled={pending}
                    onClick={() => runLifecycle(() => reactivateUserAction(user.id), () => "Account reactivated.")}
                    className="rounded-md border px-3 py-1.5 text-sm font-medium disabled:opacity-60"
                    style={{ borderColor: "#2D6A4F", color: "#2D6A4F" }}
                  >
                    Reactivate
                  </button>
                )}
                {can(user, ACTION.retire) ? (
                  confirmingRetire ? (
                    <>
                      <button
                        type="button"
                        disabled={pending}
                        onClick={() =>
                          runLifecycle(
                            () => deleteUserAction(user.id),
                            () => "Account retired. The row was kept so existing records still resolve.",
                          )
                        }
                        className="rounded-md px-3 py-1.5 text-sm font-medium text-bg-app disabled:opacity-60"
                        style={{ background: "#9B2335" }}
                      >
                        {pending ? "Working…" : "Confirm — retire"}
                      </button>
                      <button
                        type="button"
                        onClick={() => setConfirmingRetire(false)}
                        className="rounded-md border border-border px-3 py-1.5 text-sm font-medium text-text-secondary hover:bg-border"
                      >
                        Keep account
                      </button>
                    </>
                  ) : (
                    <button
                      type="button"
                      disabled={pending}
                      onClick={() => setConfirmingRetire(true)}
                      className="rounded-md border px-3 py-1.5 text-sm font-medium disabled:opacity-60"
                      style={{ borderColor: "#9B2335", color: "#9B2335" }}
                    >
                      Retire (keeps history)
                    </button>
                  )
                ) : null}
              </div>
              {confirmingRetire ? (
                <p className="mt-3 text-sm text-text-secondary">
                  Retiring clears this person&apos;s name and email, ends their sign-in and removes every role and
                  permission. The row itself is kept, because deals, documents, entities and the audit log
                  reference it. This cannot be undone.
                </p>
              ) : null}
            </>
          ) : null}
        </div>

        {error && <p className="mt-3 text-sm text-[#9B2335]">{error}</p>}
        {notice && <p className="mt-3 text-sm text-[#2D6A4F]">{notice}</p>}

        <div className="mt-5 flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            className="rounded-md border border-border px-4 py-2 text-sm font-medium text-text-secondary hover:bg-border"
          >
            {retired || !editable ? "Close" : "Cancel"}
          </button>
          {editable && !retired ? (
            <button
              type="button"
              onClick={save}
              disabled={pending}
              className="rounded-md bg-navy px-4 py-2 text-sm font-medium text-bg-app transition-opacity hover:opacity-90 disabled:opacity-60"
            >
              {pending ? "Saving…" : "Save"}
            </button>
          ) : null}
        </div>
      </div>
    </div>
  );
}

export default function UserManagement({
  initialUsers = [],
  roles = [],
  profiles = [],
  initialStatus = "",
}) {
  const [users, setUsers] = useState(initialUsers);
  const [search, setSearch] = useState("");
  const [roleFilter, setRoleFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState(initialStatus);
  const [showTest, setShowTest] = useState(false);
  const [showRetired, setShowRetired] = useState(false);
  const [editing, setEditing] = useState(null);
  const [inviting, setInviting] = useState(false);
  const [pendingError, setPendingError] = useState(null);
  const [pending, startTransition] = useTransition();

  const filtered = useMemo(() => {
    if (!search) return users;
    const q = search.toLowerCase();
    return users.filter((u) =>
      `${u.full_name || ""} ${u.email || ""} ${u.contact_email || ""}`.toLowerCase().includes(q),
    );
  }, [users, search]);

  const pendingUsers = users.filter((u) => u.status === "pending");

  function refetch(next = {}) {
    const params = listParams({
      search,
      role: roleFilter,
      status: statusFilter,
      showTest,
      showRetired,
      ...next,
    });
    startTransition(async () => {
      const res = await searchUsersAction(params);
      if (res.ok) setUsers(res.users || []);
    });
  }

  function onInvited() {
    // Re-read: the new row's actions and sign-in state come from the server.
    refetch();
  }

  function onRevoke(user) {
    startTransition(async () => {
      const res = await revokeInviteAction(user.id);
      if (!res.ok) return;
      setUsers((prev) => prev.map((u) => (u.id === user.id ? { ...u, invite_status: "revoked" } : u)));
    });
  }

  function onSaved(updated) {
    setUsers((prev) => prev.map((u) => (u.id === updated.id ? { ...u, ...updated } : u)));
    setEditing((cur) => (cur && cur.id === updated.id ? { ...cur, ...updated } : cur));
  }

  function onSavedAndClose(updated) {
    onSaved(updated);
    setEditing(null);
  }

  function decide(call) {
    setPendingError(null);
    startTransition(async () => {
      const res = await call();
      if (!res.ok) {
        setPendingError(res.error);
        return;
      }
      refetch();
    });
  }

  return (
    <div className="mt-6">
      <PendingPanel
        pending={pendingUsers}
        users={users}
        roles={roles}
        busy={pending}
        error={pendingError}
        onApprove={(u, roleId) => decide(() => approvePendingUserAction(u.id, roleId))}
        onReject={(u) => decide(() => rejectPendingUserAction(u.id))}
        onLink={(u, targetId) => decide(() => linkPendingUserAction(u.id, targetId))}
      />
      <div className="mt-6 flex flex-wrap items-center gap-3">
        <input
          type="search"
          placeholder="Search name or email…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          onBlur={() => refetch()}
          className="w-64 rounded-md border border-border bg-bg-card px-3 py-2 text-sm text-text-primary outline-none focus:ring-2 focus:ring-navy"
        />
        <select
          value={statusFilter}
          onChange={(e) => {
            setStatusFilter(e.target.value);
            refetch({ status: e.target.value });
          }}
          className="rounded-md border border-border bg-bg-card px-3 py-2 text-sm text-text-primary outline-none focus:ring-2 focus:ring-navy"
        >
          {STATUS_FILTER_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>{o.label}</option>
          ))}
        </select>
        <select
          value={roleFilter}
          onChange={(e) => {
            setRoleFilter(e.target.value);
            refetch({ role: e.target.value });
          }}
          className="rounded-md border border-border bg-bg-card px-3 py-2 text-sm text-text-primary outline-none focus:ring-2 focus:ring-navy"
        >
          <option value="">All roles</option>
          {roles.map((r) => (
            <option key={r.id} value={r.name}>{roleLabel(r.name)}</option>
          ))}
        </select>
        <label className="flex items-center gap-1.5 text-sm text-text-secondary">
          <input
            type="checkbox"
            checked={showRetired}
            onChange={(e) => {
              setShowRetired(e.target.checked);
              refetch({ showRetired: e.target.checked });
            }}
          />
          Show retired
        </label>
        <label className="flex items-center gap-1.5 text-sm text-text-secondary">
          <input
            type="checkbox"
            checked={showTest}
            onChange={(e) => {
              setShowTest(e.target.checked);
              refetch({ showTest: e.target.checked });
            }}
          />
          Show test accounts
        </label>
        {pending && <span className="text-xs text-text-muted">Loading…</span>}
        <button
          type="button"
          onClick={() => setInviting(true)}
          className="ml-auto rounded-md bg-navy px-4 py-2 text-sm font-medium text-bg-app transition-opacity hover:opacity-90"
        >
          Invite Member
        </button>
      </div>

      <div className="mt-4 overflow-hidden rounded-lg border bg-bg-card" style={{ borderColor: "#ece8dd" }}>
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-text-muted">
              <th className="px-4 py-3 font-medium">Name</th>
              <th className="px-4 py-3 font-medium">Email</th>
              <th className="px-4 py-3 font-medium">Roles</th>
              <th className="px-4 py-3 font-medium">Signs in with</th>
              <th className="px-4 py-3 font-medium">Last Sign-in</th>
              <th className="px-4 py-3 font-medium">Status</th>
              <th className="px-4 py-3 text-right font-medium">Actions</th>
            </tr>
          </thead>
          <tbody>
            {filtered.length === 0 ? (
              <tr>
                <td colSpan={7} className="px-4 py-10 text-center text-text-muted">No members found.</td>
              </tr>
            ) : (
              filtered.map((u) => <UserRow key={u.id} user={u} onEdit={setEditing} onRevoke={onRevoke} />)
            )}
          </tbody>
        </table>
      </div>

      {inviting && (
        <InviteModal profiles={profiles} onClose={() => setInviting(false)} onInvited={onInvited} />
      )}

      {editing && (
        <ManageModal
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
