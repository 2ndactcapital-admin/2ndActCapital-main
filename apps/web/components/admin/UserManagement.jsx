"use client";

import { useMemo, useState, useTransition } from "react";
import { assignRoleAction, searchUsersAction } from "@/lib/adminActions";

function roleLabel(name) {
  if (!name) return "—";
  return name
    .split("_")
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

function EditRoleModal({ user, roles, onClose, onSaved }) {
  const [roleId, setRoleId] = useState(user.role_id || "");
  const [error, setError] = useState(null);
  const [pending, startTransition] = useTransition();

  function save() {
    if (!roleId) {
      setError("Select a role.");
      return;
    }
    setError(null);
    startTransition(async () => {
      const res = await assignRoleAction(user.id, roleId);
      if (res.ok) {
        onSaved(res.user);
      } else {
        setError(res.error || "Could not update role.");
      }
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
        <h3 className="text-base font-semibold text-navy">Edit Role</h3>
        <div className="mt-3 rounded-md border border-border bg-bg-app p-3 text-sm">
          <p className="font-medium text-text-primary">
            {user.full_name || "—"}
          </p>
          <p className="text-text-muted">{user.email}</p>
        </div>

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
            onClick={save}
            disabled={pending}
            className="rounded-md bg-navy px-4 py-2 text-sm font-medium text-bg-app transition-opacity hover:opacity-90 disabled:opacity-60"
          >
            {pending ? "Saving…" : "Save"}
          </button>
        </div>
      </div>
    </div>
  );
}

export default function UserManagement({ initialUsers = [], roles = [] }) {
  const [users, setUsers] = useState(initialUsers);
  const [search, setSearch] = useState("");
  const [roleFilter, setRoleFilter] = useState("");
  const [editing, setEditing] = useState(null);
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

  function onSaved(updated) {
    setUsers((prev) =>
      prev.map((u) =>
        u.id === updated.id
          ? { ...u, role: updated.role, role_id: updated.role_id }
          : u,
      ),
    );
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
              <th className="px-4 py-3 font-medium">Status</th>
              <th className="px-4 py-3 text-right font-medium">Actions</th>
            </tr>
          </thead>
          <tbody>
            {filtered.length === 0 ? (
              <tr>
                <td
                  colSpan={5}
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
                  <td className="px-4 py-3 text-text-secondary">Active</td>
                  <td className="px-4 py-3 text-right">
                    <button
                      type="button"
                      onClick={() => setEditing(u)}
                      className="text-sm font-medium text-navy hover:underline"
                    >
                      Edit Role
                    </button>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {editing && (
        <EditRoleModal
          user={editing}
          roles={roles}
          onClose={() => setEditing(null)}
          onSaved={onSaved}
        />
      )}
    </div>
  );
}
