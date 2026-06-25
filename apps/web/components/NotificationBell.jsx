"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { IconBell } from "@tabler/icons-react";

function timeAgo(iso) {
  if (!iso) return "";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const secs = Math.max(0, Math.floor((Date.now() - then) / 1000));
  if (secs < 60) return "just now";
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  if (days < 7) return `${days}d ago`;
  return new Date(iso).toLocaleDateString();
}

// Map an event to an in-panel action link.
function actionFor(n) {
  if (!n.resource_id) return null;
  if (n.event_type === "compliance_override_requested") {
    return { label: "Review", href: `/marketplace/${n.resource_id}?tab=pipeline` };
  }
  if (n.event_type === "document_approved" || n.event_type === "document_rejected") {
    return { label: "View", href: `/marketplace/${n.resource_id}?tab=documents` };
  }
  if (n.resource_type === "deal") {
    return { label: "View", href: `/marketplace/${n.resource_id}` };
  }
  return null;
}

function isUnread(n) {
  return n.status === "pending" || n.status === "delivered";
}

export default function NotificationBell() {
  const [count, setCount] = useState(0);
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(false);
  const ref = useRef(null);

  const refreshCount = useCallback(async () => {
    try {
      const res = await fetch("/api/notifications/count", { cache: "no-store" });
      if (!res.ok) return;
      const data = await res.json();
      setCount(data.unread_count ?? 0);
    } catch {
      // network hiccup — keep last known count
    }
  }, []);

  const loadItems = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch("/api/notifications?limit=15", { cache: "no-store" });
      const data = res.ok ? await res.json() : { notifications: [] };
      setItems(data.notifications || []);
      setCount(data.unread_count ?? 0);
    } catch {
      setItems([]);
    } finally {
      setLoading(false);
    }
  }, []);

  // Poll the unread count every 60s.
  useEffect(() => {
    refreshCount();
    const interval = setInterval(refreshCount, 60000);
    return () => clearInterval(interval);
  }, [refreshCount]);

  // Close on outside click.
  useEffect(() => {
    function handle(e) {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false);
    }
    document.addEventListener("mousedown", handle);
    return () => document.removeEventListener("mousedown", handle);
  }, []);

  function toggle() {
    const next = !open;
    setOpen(next);
    if (next) loadItems();
  }

  async function markRead(id) {
    try {
      await fetch(`/api/notifications/${id}/read`, { method: "PUT" });
      setItems((prev) =>
        prev.map((n) => (n.id === id ? { ...n, status: "read" } : n)),
      );
      setCount((c) => Math.max(0, c - 1));
    } catch {
      // ignore
    }
  }

  async function markAllRead() {
    try {
      await fetch("/api/notifications", { method: "PUT" });
      setItems((prev) => prev.map((n) => ({ ...n, status: "read" })));
      setCount(0);
    } catch {
      // ignore
    }
  }

  const badge = count > 9 ? "9+" : String(count);

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        onClick={toggle}
        aria-label="Notifications"
        aria-expanded={open}
        className="relative flex h-9 w-9 items-center justify-center rounded-md transition-opacity hover:opacity-80"
      >
        <IconBell size={20} stroke={1.75} className="text-bg-app" />
        {count > 0 && (
          <span
            className="absolute -right-0.5 -top-0.5 flex min-w-[16px] items-center justify-center rounded-full px-1 text-[10px] font-semibold text-navy"
            style={{ backgroundColor: "#C5A880", height: 16 }}
          >
            {badge}
          </span>
        )}
      </button>

      {open && (
        <div
          className="absolute right-0 top-full z-50 mt-1 w-[380px] rounded-lg border bg-bg-card"
          style={{
            borderColor: "#ece8dd",
            boxShadow: "0 1px 3px rgba(0,0,0,0.06)",
          }}
        >
          <div className="flex items-center justify-between border-b border-border px-4 py-3">
            <p className="text-sm font-semibold text-navy">Notifications</p>
            <button
              type="button"
              onClick={markAllRead}
              className="text-xs font-medium text-text-muted hover:text-navy"
            >
              Mark all read
            </button>
          </div>

          <div className="max-h-[480px] overflow-y-auto">
            {loading ? (
              <p className="px-4 py-8 text-center text-sm text-text-muted">
                Loading…
              </p>
            ) : items.length === 0 ? (
              <p className="px-4 py-10 text-center text-sm text-text-muted">
                You&apos;re all caught up ✓
              </p>
            ) : (
              items.map((n) => {
                const unread = isUnread(n);
                const action = actionFor(n);
                return (
                  <div
                    key={n.id}
                    className="border-b border-border px-4 py-3 last:border-b-0"
                    style={
                      unread
                        ? {
                            borderLeft: "3px solid #C5A880",
                            backgroundColor: "#FAF9F6",
                          }
                        : { borderLeft: "3px solid transparent" }
                    }
                  >
                    <div className="flex items-start justify-between gap-2">
                      <p className="text-sm font-medium text-text-primary">
                        {n.title}
                      </p>
                      <span className="shrink-0 text-[11px] text-text-muted">
                        {timeAgo(n.created_at)}
                      </span>
                    </div>
                    <p className="mt-0.5 text-sm text-text-secondary">{n.body}</p>
                    <div className="mt-1.5 flex items-center gap-3">
                      {action && (
                        <a
                          href={action.href}
                          onClick={() => markRead(n.id)}
                          className="text-xs font-medium text-navy hover:underline"
                        >
                          {action.label} →
                        </a>
                      )}
                      {unread && (
                        <button
                          type="button"
                          onClick={() => markRead(n.id)}
                          className="text-xs text-text-muted hover:text-navy"
                        >
                          Mark read
                        </button>
                      )}
                    </div>
                  </div>
                );
              })
            )}
          </div>

          <div className="border-t border-border px-4 py-2 text-center">
            <a
              href="/notifications"
              className="text-xs font-medium text-navy hover:underline"
            >
              View all
            </a>
          </div>
        </div>
      )}
    </div>
  );
}
