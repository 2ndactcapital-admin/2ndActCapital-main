"use client";

import { useEffect, useState } from "react";
import { formatCurrency } from "@/lib/format";

// ---------------------------------------------------------------------------
// Narration
// ---------------------------------------------------------------------------

function Narration({ text }) {
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    if (text) {
      const t = setTimeout(() => setVisible(true), 40);
      return () => clearTimeout(t);
    }
  }, [text]);
  if (!text) return null;
  return (
    <p
      className="mt-2 text-base text-[#334155] leading-relaxed transition-opacity duration-500"
      style={{ opacity: visible ? 1 : 0, fontFamily: "Spectral, Georgia, serif", fontStyle: "italic" }}
    >
      {text}
    </p>
  );
}

// ---------------------------------------------------------------------------
// Block cards
// ---------------------------------------------------------------------------

function TodoItem({ item, onDismiss, onComplete }) {
  return (
    <div className="flex items-start justify-between gap-3 py-2 border-b border-[#F5F1EB] last:border-0">
      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium text-[#0F172A] truncate">{item.title}</p>
        {item.body && (
          <p className="text-xs text-[#64748B] mt-0.5">{item.body}</p>
        )}
      </div>
      <div className="flex gap-1.5 shrink-0">
        {item.action_href && (
          <a
            href={item.action_href}
            className="rounded px-2.5 py-1 text-xs font-medium"
            style={{ background: "#1B2B4B", color: "#FAF9F6" }}
          >
            {item.action_label || "View"}
          </a>
        )}
        <button
          type="button"
          onClick={() => onDismiss(item.id)}
          className="rounded px-2 py-1 text-xs text-[#64748B] hover:text-[#0F172A] transition-colors"
          title="Dismiss"
        >
          ✕
        </button>
      </div>
    </div>
  );
}

function NeedsAttentionBlock({ data, onDismiss }) {
  if (!data?.items?.length) return null;
  return (
    <div className="rounded-lg border border-[#ece8dd] bg-white p-5">
      <h3 className="text-xs font-semibold uppercase tracking-wider text-[#C5A880] mb-3">
        Needs your attention
      </h3>
      <div>
        {data.items.map((item) => (
          <TodoItem
            key={item.id}
            item={item}
            onDismiss={onDismiss}
            onComplete={() => {}}
          />
        ))}
      </div>
    </div>
  );
}

function NewDealsBlock({ data }) {
  if (!data?.items?.length) return null;
  return (
    <div className="rounded-lg border border-[#ece8dd] bg-white p-5">
      <h3 className="text-xs font-semibold uppercase tracking-wider text-[#C5A880] mb-3">
        New deals
      </h3>
      <div className="space-y-2">
        {data.items.map((deal) => (
          <a
            key={deal.id}
            href={`/marketplace/${deal.id}`}
            className="flex items-center justify-between rounded p-2 hover:bg-[#FAF9F6] transition-colors group"
          >
            <span className="text-sm font-medium text-[#0F172A] group-hover:text-[#C5A880] transition-colors">
              {deal.name}
            </span>
            {deal.target_raise && (
              <span className="text-xs text-[#64748B] tabular-nums">
                {formatCurrency(deal.target_raise)}
              </span>
            )}
          </a>
        ))}
      </div>
    </div>
  );
}

function MyPositionsBlock({ data }) {
  if (!data?.items?.length) return null;
  return (
    <div className="rounded-lg border border-[#ece8dd] bg-white p-5">
      <h3 className="text-xs font-semibold uppercase tracking-wider text-[#C5A880] mb-3">
        My positions
      </h3>
      <div className="space-y-1.5">
        {data.items.map((pos) => (
          <div key={pos.id} className="flex items-center justify-between text-sm py-1">
            <span className="font-medium text-[#0F172A]">{pos.deal_name}</span>
            <span className="text-xs text-[#64748B] capitalize">
              {pos.investment_stage?.replace(/_/g, " ")}
            </span>
          </div>
        ))}
      </div>
      <a
        href="/portfolio"
        className="mt-3 block text-xs font-medium text-[#C5A880] hover:text-[#1B2B4B] transition-colors"
      >
        View all positions →
      </a>
    </div>
  );
}

function OnHorizonBlock({ data, onDismiss }) {
  if (!data?.items?.length) return null;
  return (
    <div className="rounded-lg border border-[#ece8dd] bg-white p-5">
      <h3 className="text-xs font-semibold uppercase tracking-wider text-[#C5A880] mb-3">
        On the horizon
      </h3>
      <div>
        {data.items.map((item) => (
          <div key={item.id} className="flex items-start justify-between gap-3 py-2 border-b border-[#F5F1EB] last:border-0">
            <div className="flex-1 min-w-0">
              <p className="text-sm text-[#334155] truncate">{item.title}</p>
              {item.body && (
                <p className="text-xs text-[#64748B] mt-0.5">{item.body}</p>
              )}
            </div>
            <button
              type="button"
              onClick={() => onDismiss(item.id)}
              className="text-xs text-[#64748B] hover:text-[#0F172A] shrink-0 transition-colors"
            >
              ✕
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}

function PipelineBlock({ data }) {
  if (!data?.by_status) return null;
  return (
    <div className="rounded-lg border border-[#ece8dd] bg-white p-5">
      <h3 className="text-xs font-semibold uppercase tracking-wider text-[#C5A880] mb-3">
        Pipeline snapshot
      </h3>
      <dl className="grid grid-cols-2 gap-3">
        {Object.entries(data.by_status).map(([status, count]) => (
          <div key={status}>
            <dt className="text-xs text-[#64748B] capitalize">{status.replace(/_/g, " ")}</dt>
            <dd className="text-lg font-medium text-[#0F172A] tabular-nums">{count}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

function SPVActivityBlock({ data }) {
  if (!data) return null;
  return (
    <div className="rounded-lg border border-[#ece8dd] bg-white p-5">
      <h3 className="text-xs font-semibold uppercase tracking-wider text-[#C5A880] mb-3">
        SPV activity
      </h3>
      {data.soft_subscriptions > 0 && (
        <p className="text-sm text-[#334155] mb-3">
          {data.soft_subscriptions} soft subscription{data.soft_subscriptions !== 1 ? "s" : ""} pending
        </p>
      )}
      {data.recent_changes?.length > 0 && (
        <div className="space-y-1.5">
          {data.recent_changes.map((ch, i) => (
            <div key={i} className="text-xs text-[#64748B]">
              <span className="font-medium text-[#0F172A]">{ch.spv_name}</span>
              {" · "}{ch.from ? `${ch.from} → ` : ""}{ch.to}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function BlockCard({ block, onDismiss }) {
  switch (block.key) {
    case "needs_attention":
      return <NeedsAttentionBlock data={block.data} onDismiss={onDismiss} />;
    case "new_deals":
      return <NewDealsBlock data={block.data} />;
    case "my_positions":
      return <MyPositionsBlock data={block.data} />;
    case "on_horizon":
      return <OnHorizonBlock data={block.data} onDismiss={onDismiss} />;
    case "pipeline_snapshot":
      return <PipelineBlock data={block.data} />;
    case "spv_activity":
      return <SPVActivityBlock data={block.data} />;
    default:
      return null;
  }
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export default function DashboardBrief({ greeting }) {
  const [blocks, setBlocks] = useState([]);
  const [narration, setNarration] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;

    async function loadBrief() {
      try {
        // Kick off todo regeneration in the background (fire-and-forget).
        fetch("/api/dashboard/todos/regenerate", { method: "POST" }).catch(() => {});

        const res = await fetch("/api/dashboard/brief");
        if (!res.ok) return;
        const data = await res.json();
        if (active) {
          setBlocks(data.blocks || []);
          setLoading(false);
        }

        // Lazy-load narration after blocks render.
        if (data.narration_pending) {
          const nRes = await fetch("/api/dashboard/brief/narration");
          if (nRes.ok) {
            const nData = await nRes.json();
            if (active) setNarration(nData.narration || null);
          }
        }
      } catch {
        if (active) setLoading(false);
      }
    }

    loadBrief();
    return () => { active = false; };
  }, []);

  async function dismissTodo(id) {
    setBlocks((prev) =>
      prev.map((b) => ({
        ...b,
        data: {
          ...b.data,
          items: (b.data.items || []).filter((item) => item.id !== id),
        },
      }))
    );
    await fetch(`/api/dashboard/todos/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ dismissed: true }),
    }).catch(() => {});
  }

  const visibleBlocks = blocks.filter(
    (b) => (b.data?.items?.length || b.data?.by_status || b.data?.soft_subscriptions !== undefined)
  );

  return (
    <div>
      {/* Greeting + narration */}
      <div className="mb-8">
        <h1
          className="text-2xl font-light text-[#1B2B4B]"
          style={{ fontFamily: "Spectral, Georgia, serif" }}
        >
          {greeting}
        </h1>
        {narration && <Narration text={narration} />}
      </div>

      {loading && (
        <div className="grid gap-5 sm:grid-cols-2">
          {[1, 2, 3].map((i) => (
            <div
              key={i}
              className="rounded-lg border border-[#ece8dd] bg-white p-5 h-24 animate-pulse"
              style={{ background: "#F5F1EB" }}
            />
          ))}
        </div>
      )}

      {!loading && visibleBlocks.length === 0 && (
        <div className="rounded-lg border border-[#ece8dd] bg-white p-8 text-center">
          <p className="text-sm text-[#64748B]">
            Your brief is quiet today. Check back tomorrow.
          </p>
        </div>
      )}

      {!loading && visibleBlocks.length > 0 && (
        <div className="grid gap-5 sm:grid-cols-2">
          {visibleBlocks.map((block) => (
            <BlockCard key={block.key} block={block} onDismiss={dismissTodo} />
          ))}
        </div>
      )}
    </div>
  );
}
