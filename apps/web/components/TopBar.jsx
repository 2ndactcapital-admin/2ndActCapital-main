"use client";

import { useState, useRef, useEffect } from "react";
import {
  IconChevronDown,
  IconSettings,
  IconUser,
  IconLogout,
} from "@tabler/icons-react";

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
  const [open, setOpen] = useState(false);
  const ref = useRef(null);

  useEffect(() => {
    function handle(e) {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false);
    }
    document.addEventListener("mousedown", handle);
    return () => document.removeEventListener("mousedown", handle);
  }, []);

  return (
    <header className="flex h-16 items-center justify-between bg-navy px-6">
      <div className="flex items-center gap-3">
        <div className="h-8 w-8 rounded-md bg-gold" aria-hidden="true" />
        <span className="text-lg font-medium text-bg-app">2nd Act Capital</span>
      </div>

      <div className="relative" ref={ref}>
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="flex items-center gap-2 rounded-md px-2 py-1 transition-opacity hover:opacity-80"
          aria-expanded={open}
          aria-haspopup="true"
        >
          <div
            className="flex h-9 w-9 items-center justify-center rounded-full bg-gold text-sm font-semibold"
            style={{ color: "#4a3a1f" }}
          >
            {initials}
          </div>
          <span className="text-sm font-medium text-bg-app">{displayName}</span>
          <IconChevronDown
            size={16}
            stroke={2}
            className={`text-bg-app/70 transition-transform ${open ? "rotate-180" : ""}`}
          />
        </button>

        {open && (
          <div className="absolute right-0 top-full z-50 mt-1 w-56 rounded-lg border border-border bg-bg-card shadow-lg">
            <div className="border-b border-border px-4 py-3">
              <p className="text-sm font-medium text-text-primary">{displayName}</p>
              {user?.email && user.email !== displayName && (
                <p className="mt-0.5 text-xs text-text-muted">{user.email}</p>
              )}
            </div>
            <div className="p-1">
              <a
                href="/settings"
                className="flex items-center gap-2 rounded-md px-3 py-2 text-sm text-text-secondary hover:bg-border"
                onClick={() => setOpen(false)}
              >
                <IconSettings size={16} stroke={1.75} />
                Settings
              </a>
              <a
                href="/investment-profile"
                className="flex items-center gap-2 rounded-md px-3 py-2 text-sm text-text-secondary hover:bg-border"
                onClick={() => setOpen(false)}
              >
                <IconUser size={16} stroke={1.75} />
                Investment Profile
              </a>
              <div className="my-1 border-t border-border" />
              <a
                href="/auth/logout"
                className="flex items-center gap-2 rounded-md px-3 py-2 text-sm text-[#9B2335] hover:bg-[#FEF2F2]"
              >
                <IconLogout size={16} stroke={1.75} />
                Sign Out
              </a>
            </div>
          </div>
        )}
      </div>
    </header>
  );
}
