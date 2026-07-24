"use client";

import { useState, useTransition } from "react";
import PermissionChecklist from "@/components/admin/PermissionChecklist";
import {
  assignPermissionSetAction,
  createPermissionSetAction,
  deletePermissionSetAction,
  removePermissionSetAction,
  togglePermissionSetPermissionAction,
} from "@/lib/permissionActions";

const CARD = {
  borderColor: "#ece8dd",
  boxShadow: "0 1px 3px rgba(0,0,0,0.06)",
};

function userLabel(u) {
  return u.full_name || u.email || u.id;
}

function Card({ title, hint, children }) {
  return (
    <section className="rounded-lg border bg-bg-card p-5" style={CARD}>
      <h2 className="text-base font-semibold text-navy">{title}</h2>
      {hint && <p className="mt-1 text-sm text-text-muted">{hint}</p>}
      <div className="mt-4">{children}</div>
    </section>
  );
}

function inputClass() {
  return "rounded-md border border-border bg-bg-card px-3 py-2 text-sm text-text-primary outline-none focus:ring-2 focus:ring-navy";
}

export default function PermissionSetsManager({
  initialSets = [],
  permissions = [],
  users = [],
}) {
  const [sets, setSets] = useState(initialSets);
  const [expandedId, setExpandedId] = useState(null);
  const [busyKey, setBusyKey] = useState(null);
  const [pending, startTransition] = useTransition();
  const [error, setError] = useState(null);

  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");
  // Per-set "assign a user" selection.
  const [assignUserId, setAssignUserId] = useState({});

  function submitSet() {
    if (!name.trim()) {
      setError("Permission set name is required.");
      return;
    }
    setError(null);
    startTransition(async () => {
      const res = await createPermissionSetAction(name.trim(), desc.trim());
      if (res.ok) {
        setSets((prev) => [...prev, res.set]);
        setName("");
        setDesc("");
        setExpandedId(res.set.id);
      } else {
        setError(res.error || "Could not create permission set.");
      }
    });
  }

  function togglePermission(setId, permissionKey, nextGranted) {
    setError(null);
    setBusyKey(permissionKey);
    startTransition(async () => {
      const res = await togglePermissionSetPermissionAction(
        setId,
        permissionKey,
        nextGranted,
      );
      if (res.ok) {
        setSets((prev) =>
          prev.map((s) =>
            s.id === setId
              ? { ...s, permission_keys: res.permissionKeys }
              : s,
          ),
        );
      } else {
        setError(res.error || "Could not update permission.");
      }
      setBusyKey(null);
    });
  }

  function removeSet(set) {
    if (
      !window.confirm(
        `Delete permission set “${set.name}”? It will be removed from ${set.users.length} member(s).`,
      )
    ) {
      return;
    }
    setError(null);
    startTransition(async () => {
      const res = await deletePermissionSetAction(set.id);
      if (res.ok) {
        setSets(res.sets);
        if (expandedId === set.id) setExpandedId(null);
      } else {
        setError(res.error || "Could not delete permission set.");
      }
    });
  }

  function assignUser(setId) {
    const userId = assignUserId[setId];
    if (!userId) {
      setError("Pick a member to assign.");
      return;
    }
    setError(null);
    startTransition(async () => {
      const res = await assignPermissionSetAction(setId, userId);
      if (res.ok) {
        setSets(res.sets);
        setAssignUserId((prev) => ({ ...prev, [setId]: "" }));
      } else {
        setError(res.error || "Could not assign permission set.");
      }
    });
  }

  function unassignUser(setId, userId) {
    setError(null);
    startTransition(async () => {
      const res = await removePermissionSetAction(setId, userId);
      if (res.ok) {
        setSets(res.sets);
      } else {
        setError(res.error || "Could not remove permission set.");
      }
    });
  }

  return (
    <div className="mt-6 space-y-6">
      {error && (
        <div className="rounded-md border border-border bg-bg-card px-4 py-2 text-sm text-[#9B2335]">
          {error}
        </div>
      )}
      {pending && <p className="text-xs text-text-muted">Working…</p>}

      <Card
        title="Create a permission set"
        hint="An additive bundle of grants that can be layered onto a member on top of their profile."
      >
        <div className="flex flex-wrap items-end gap-3">
          <div className="flex flex-col">
            <label className="text-xs font-medium uppercase tracking-wide text-text-muted">
              Name
            </label>
            <input
              className={`mt-1 w-56 ${inputClass()}`}
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. Compliance Override"
            />
          </div>
          <div className="flex flex-col">
            <label className="text-xs font-medium uppercase tracking-wide text-text-muted">
              Description
            </label>
            <input
              className={`mt-1 w-72 ${inputClass()}`}
              value={desc}
              onChange={(e) => setDesc(e.target.value)}
              placeholder="Optional"
            />
          </div>
          <button
            type="button"
            onClick={submitSet}
            disabled={pending}
            className="rounded-md bg-navy px-4 py-2 text-sm font-medium text-bg-app hover:opacity-90 disabled:opacity-60"
          >
            Create set
          </button>
        </div>
      </Card>

      <Card
        title="Permission sets"
        hint="Expand to edit grants; assign a set to layer it onto a specific member."
      >
        {sets.length === 0 ? (
          <p className="text-sm text-text-muted">No permission sets yet.</p>
        ) : (
          <ul className="space-y-3">
            {sets.map((s) => {
              const open = expandedId === s.id;
              return (
                <li
                  key={s.id}
                  className="rounded-md border border-border bg-bg-app"
                >
                  <div className="flex flex-wrap items-center gap-3 p-3">
                    <div className="min-w-0">
                      <p className="text-sm font-medium text-text-primary">
                        {s.name}
                      </p>
                      {s.description && (
                        <p className="text-xs text-text-muted">
                          {s.description}
                        </p>
                      )}
                    </div>
                    <span className="text-xs text-text-muted">
                      {(s.permission_keys || []).length} grant
                      {(s.permission_keys || []).length === 1 ? "" : "s"} ·{" "}
                      {s.users.length} member
                      {s.users.length === 1 ? "" : "s"}
                    </span>
                    <div className="ml-auto flex items-center gap-3">
                      <button
                        type="button"
                        onClick={() => setExpandedId(open ? null : s.id)}
                        className="text-sm font-medium text-navy hover:underline"
                      >
                        {open ? "Close" : "Edit grants"}
                      </button>
                      <button
                        type="button"
                        onClick={() => removeSet(s)}
                        disabled={pending}
                        className="text-sm font-medium text-[#9B2335] hover:underline disabled:opacity-40"
                      >
                        Delete
                      </button>
                    </div>
                  </div>

                  {/* Assigned members */}
                  <div className="flex flex-wrap items-center gap-2 border-t border-border px-3 py-2">
                    {s.users.length === 0 ? (
                      <span className="text-xs text-text-muted">
                        No members assigned.
                      </span>
                    ) : (
                      s.users.map((m) => (
                        <span
                          key={m.user_id}
                          className="inline-flex items-center gap-1 rounded-full bg-gold-light px-2.5 py-0.5 text-xs font-medium text-navy"
                        >
                          {userLabel(m)}
                          <button
                            type="button"
                            onClick={() => unassignUser(s.id, m.user_id)}
                            className="text-navy/70 hover:text-navy"
                            aria-label="Remove assignment"
                          >
                            ×
                          </button>
                        </span>
                      ))
                    )}
                    <div className="ml-auto flex items-center gap-2">
                      <select
                        className={inputClass()}
                        value={assignUserId[s.id] || ""}
                        onChange={(e) =>
                          setAssignUserId((prev) => ({
                            ...prev,
                            [s.id]: e.target.value,
                          }))
                        }
                      >
                        <option value="">Assign a member…</option>
                        {users.map((u) => (
                          <option key={u.id} value={u.id}>
                            {userLabel(u)}
                          </option>
                        ))}
                      </select>
                      <button
                        type="button"
                        onClick={() => assignUser(s.id)}
                        disabled={pending}
                        className="rounded-md border border-navy px-3 py-2 text-sm font-medium text-navy hover:bg-navy hover:text-bg-app disabled:opacity-60"
                      >
                        Assign
                      </button>
                    </div>
                  </div>

                  {open && (
                    <div className="border-t border-border p-3">
                      <PermissionChecklist
                        permissions={permissions}
                        granted={s.permission_keys}
                        busyKey={busyKey}
                        disabled={pending && busyKey === null}
                        onToggle={(key, next) =>
                          togglePermission(s.id, key, next)
                        }
                      />
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </Card>
    </div>
  );
}
