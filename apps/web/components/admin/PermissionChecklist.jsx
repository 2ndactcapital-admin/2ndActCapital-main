"use client";

// Shared action-registry grant checklist, reused by the Profiles and Permission
// Sets screens. Renders one toggle per action-registry permission, grouped by
// resource, and calls `onToggle(permissionKey, nextGranted)` when a row flips.
// The parent owns persistence and the granted set; this component is presentational.

function label(key) {
  return key
    .split("_")
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

export default function PermissionChecklist({
  permissions = [],
  granted = [],
  onToggle,
  disabled = false,
  busyKey = null,
}) {
  const grantedSet = new Set(granted);

  // Group by resource for a scannable layout.
  const groups = {};
  for (const p of permissions) {
    (groups[p.resource] ||= []).push(p);
  }
  const resources = Object.keys(groups).sort();

  if (permissions.length === 0) {
    return (
      <p className="text-sm text-text-muted">No permissions in the registry.</p>
    );
  }

  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
      {resources.map((resource) => (
        <div
          key={resource}
          className="rounded-md border border-border bg-bg-app p-3"
        >
          <p className="text-xs font-semibold uppercase tracking-wide text-text-muted">
            {label(resource)}
          </p>
          <ul className="mt-2 space-y-1.5">
            {groups[resource].map((p) => {
              const on = grantedSet.has(p.name);
              return (
                <li key={p.name}>
                  <label className="flex items-center gap-2 text-sm text-text-primary">
                    <input
                      type="checkbox"
                      checked={on}
                      disabled={disabled || busyKey === p.name}
                      onChange={() => onToggle(p.name, !on)}
                      className="h-4 w-4 rounded border-border text-navy accent-navy focus:ring-navy disabled:opacity-50"
                    />
                    <span className={busyKey === p.name ? "opacity-60" : ""}>
                      {label(p.action)}
                    </span>
                    <span className="ml-auto font-mono text-[11px] text-text-muted">
                      {p.name}
                    </span>
                  </label>
                </li>
              );
            })}
          </ul>
        </div>
      ))}
    </div>
  );
}
