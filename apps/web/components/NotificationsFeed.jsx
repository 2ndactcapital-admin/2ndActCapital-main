"use client";

import { useMemo, useState } from "react";

const TABS = [
  { key: "all", label: "All" },
  { key: "unread", label: "Unread" },
  { key: "deals", label: "Deals" },
  { key: "compliance", label: "Compliance" },
  { key: "documents", label: "Documents" },
];

const PAGE_SIZE = 20;

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

const isUnread = (n) => n.status === "pending" || n.status === "delivered";

function matchesTab(n, tab) {
  switch (tab) {
    case "unread":
      return isUnread(n);
    case "deals":
      return [
        "deal_stage_changed",
        "ioi_confirmed",
        "new_deal_published",
        "investment_stage_changed",
      ].includes(n.event_type);
    case "compliance":
      return n.event_type.startsWith("compliance_");
    case "documents":
      return n.event_type.startsWith("document_");
    default:
      return true;
  }
}

export default function NotificationsFeed({ initialItems = [] }) {
  const [items, setItems] = useState(initialItems);
  const [tab, setTab] = useState("all");
  const [offset, setOffset] = useState(initialItems.length);
  const [hasMore, setHasMore] = useState(initialItems.length >= PAGE_SIZE);
  const [loading, setLoading] = useState(false);

  const visible = useMemo(
    () => items.filter((n) => matchesTab(n, tab)),
    [items, tab],
  );

  async function loadMore() {
    setLoading(true);
    try {
      const res = await fetch(
        `/api/notifications?limit=${PAGE_SIZE}&offset=${offset}`,
        { cache: "no-store" },
      );
      const data = res.ok ? await res.json() : { notifications: [] };
      const next = data.notifications || [];
      setItems((prev) => [...prev, ...next]);
      setOffset((o) => o + next.length);
      setHasMore(next.length >= PAGE_SIZE);
    } catch {
      setHasMore(false);
    } finally {
      setLoading(false);
    }
  }

  async function markRead(id) {
    try {
      await fetch(`/api/notifications/${id}/read`, { method: "PUT" });
      setItems((prev) =>
        prev.map((n) => (n.id === id ? { ...n, status: "read" } : n)),
      );
    } catch {
      // ignore
    }
  }

  async function markAllRead() {
    try {
      await fetch("/api/notifications", { method: "PUT" });
      setItems((prev) => prev.map((n) => ({ ...n, status: "read" })));
    } catch {
      // ignore
    }
  }

  return (
    <div className="mt-6">
      <div className="flex items-center justify-between">
        <div className="flex flex-wrap gap-1 border-b border-border">
          {TABS.map((t) => (
            <button
              key={t.key}
              type="button"
              onClick={() => setTab(t.key)}
              className={`-mb-px border-b-2 px-4 py-2 text-sm font-medium transition-colors ${
                tab === t.key
                  ? "border-navy text-navy"
                  : "border-transparent text-text-muted hover:text-text-secondary"
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>
        <button
          type="button"
          onClick={markAllRead}
          className="text-sm font-medium text-text-muted hover:text-navy"
        >
          Mark all as read
        </button>
      </div>

      <div className="mt-4 space-y-3">
        {visible.length === 0 ? (
          <div className="rounded-lg border border-border bg-bg-card p-10 text-center text-sm text-text-muted">
            You&apos;re all caught up ✓
          </div>
        ) : (
          visible.map((n) => {
            const unread = isUnread(n);
            const action = actionFor(n);
            return (
              <div
                key={n.id}
                className="rounded-lg border bg-bg-card p-4"
                style={{
                  borderColor: "#ece8dd",
                  borderLeft: unread
                    ? "3px solid #C5A880"
                    : "3px solid transparent",
                  backgroundColor: unread ? "#FAF9F6" : "#ffffff",
                }}
              >
                <div className="flex items-start justify-between gap-2">
                  <p className="font-medium text-text-primary">{n.title}</p>
                  <span className="shrink-0 text-xs text-text-muted">
                    {timeAgo(n.created_at)}
                  </span>
                </div>
                <p className="mt-1 text-sm text-text-secondary">{n.body}</p>
                <div className="mt-2 flex items-center gap-4">
                  {action && (
                    <a
                      href={action.href}
                      onClick={() => markRead(n.id)}
                      className="text-sm font-medium text-navy hover:underline"
                    >
                      {action.label} →
                    </a>
                  )}
                  {unread && (
                    <button
                      type="button"
                      onClick={() => markRead(n.id)}
                      className="text-sm text-text-muted hover:text-navy"
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

      {hasMore && (
        <div className="mt-6 text-center">
          <button
            type="button"
            onClick={loadMore}
            disabled={loading}
            className="rounded-md border border-border bg-bg-card px-4 py-2 text-sm font-medium text-navy transition-colors hover:bg-border disabled:opacity-60"
          >
            {loading ? "Loading…" : "Load more"}
          </button>
        </div>
      )}
    </div>
  );
}
