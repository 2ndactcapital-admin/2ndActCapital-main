import { IconSettings } from "@tabler/icons-react";

function initialsFrom(user) {
  const source = user?.name || user?.email || "";
  const parts = source.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

export default function TopBar({ user }) {
  const initials = initialsFrom(user);
  const displayName = user?.name || user?.email || "Member";

  return (
    <header className="flex h-16 items-center justify-between bg-navy px-6">
      {/* Left: logo + wordmark */}
      <div className="flex items-center gap-3">
        <div className="h-8 w-8 rounded-md bg-gold" aria-hidden="true" />
        <span className="text-lg font-medium text-bg-app">2nd Act Capital</span>
      </div>

      {/* Right: avatar, name, settings */}
      <div className="flex items-center gap-4">
        <div className="flex items-center gap-3">
          <div
            className="flex h-9 w-9 items-center justify-center rounded-full bg-gold text-sm font-semibold"
            style={{ color: "#4a3a1f" }}
          >
            {initials}
          </div>
          <span className="text-sm font-medium text-bg-app">{displayName}</span>
        </div>
        <a
          href="/settings"
          aria-label="Settings"
          className="text-bg-app transition-opacity hover:opacity-80"
        >
          <IconSettings size={20} stroke={1.75} />
        </a>
      </div>
    </header>
  );
}
