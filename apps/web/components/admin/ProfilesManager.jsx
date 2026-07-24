"use client";

import { useState, useTransition } from "react";
import PermissionChecklist from "@/components/admin/PermissionChecklist";
import {
  createProfileAction,
  deleteProfileAction,
  toggleProfilePermissionAction,
} from "@/lib/permissionActions";

const CARD = {
  borderColor: "#ece8dd",
  boxShadow: "0 1px 3px rgba(0,0,0,0.06)",
};

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

export default function ProfilesManager({
  initialProfiles = [],
  permissions = [],
}) {
  const [profiles, setProfiles] = useState(initialProfiles);
  const [expandedId, setExpandedId] = useState(null);
  const [busyKey, setBusyKey] = useState(null);
  const [pending, startTransition] = useTransition();
  const [error, setError] = useState(null);

  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");

  function submitProfile() {
    if (!name.trim()) {
      setError("Profile name is required.");
      return;
    }
    setError(null);
    startTransition(async () => {
      const res = await createProfileAction(name.trim(), desc.trim());
      if (res.ok) {
        setProfiles((prev) => [...prev, res.profile]);
        setName("");
        setDesc("");
        setExpandedId(res.profile.id);
      } else {
        setError(res.error || "Could not create profile.");
      }
    });
  }

  function togglePermission(profileId, permissionKey, nextGranted) {
    setError(null);
    setBusyKey(permissionKey);
    startTransition(async () => {
      const res = await toggleProfilePermissionAction(
        profileId,
        permissionKey,
        nextGranted,
      );
      if (res.ok) {
        setProfiles((prev) =>
          prev.map((p) =>
            p.id === profileId
              ? { ...p, permission_keys: res.permissionKeys }
              : p,
          ),
        );
      } else {
        setError(res.error || "Could not update permission.");
      }
      setBusyKey(null);
    });
  }

  function removeProfile(profile) {
    if (
      !window.confirm(
        `Delete profile “${profile.name}”? This cannot be undone.`,
      )
    ) {
      return;
    }
    setError(null);
    startTransition(async () => {
      const res = await deleteProfileAction(profile.id);
      if (res.ok) {
        setProfiles(res.profiles);
        if (expandedId === profile.id) setExpandedId(null);
      } else {
        setError(res.error || "Could not delete profile.");
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
        title="Create a profile"
        hint="A persona whose base permission grants apply to every assigned member."
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
              placeholder="e.g. Adviser"
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
            onClick={submitProfile}
            disabled={pending}
            className="rounded-md bg-navy px-4 py-2 text-sm font-medium text-bg-app hover:opacity-90 disabled:opacity-60"
          >
            Create profile
          </button>
        </div>
      </Card>

      <Card
        title="Profiles"
        hint="Expand a profile to edit its permission grants."
      >
        {profiles.length === 0 ? (
          <p className="text-sm text-text-muted">No profiles yet.</p>
        ) : (
          <ul className="space-y-3">
            {profiles.map((p) => {
              const open = expandedId === p.id;
              return (
                <li
                  key={p.id}
                  className="rounded-md border border-border bg-bg-app"
                >
                  <div className="flex flex-wrap items-center gap-3 p-3">
                    <div className="min-w-0">
                      <p className="text-sm font-medium text-text-primary">
                        {p.name}
                        {p.is_seed && (
                          <span className="ml-2 inline-flex items-center rounded-full bg-gold-light px-2 py-0.5 text-[11px] font-medium text-navy">
                            Seeded
                          </span>
                        )}
                      </p>
                      {p.description && (
                        <p className="text-xs text-text-muted">
                          {p.description}
                        </p>
                      )}
                    </div>
                    <span className="text-xs text-text-muted">
                      {(p.permission_keys || []).length} grant
                      {(p.permission_keys || []).length === 1 ? "" : "s"} ·{" "}
                      {p.user_count} member
                      {p.user_count === 1 ? "" : "s"}
                    </span>
                    <div className="ml-auto flex items-center gap-3">
                      <button
                        type="button"
                        onClick={() => setExpandedId(open ? null : p.id)}
                        className="text-sm font-medium text-navy hover:underline"
                      >
                        {open ? "Close" : "Edit grants"}
                      </button>
                      {!p.is_seed && (
                        <button
                          type="button"
                          onClick={() => removeProfile(p)}
                          disabled={pending || p.user_count > 0}
                          title={
                            p.user_count > 0
                              ? "Reassign members before deleting"
                              : undefined
                          }
                          className="text-sm font-medium text-[#9B2335] hover:underline disabled:opacity-40 disabled:no-underline"
                        >
                          Delete
                        </button>
                      )}
                    </div>
                  </div>
                  {open && (
                    <div className="border-t border-border p-3">
                      <PermissionChecklist
                        permissions={permissions}
                        granted={p.permission_keys}
                        busyKey={busyKey}
                        disabled={pending && busyKey === null}
                        onToggle={(key, next) =>
                          togglePermission(p.id, key, next)
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
