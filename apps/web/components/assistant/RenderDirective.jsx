"use client";

import SPVList from "./render/SPVList";
import CapTable from "./render/CapTable";

// Maps render.component string → React component.
// Unknown components fall back to a plain text display.

function DealList({ deals = [] }) {
  if (!deals.length) return <p className="text-sm text-slate-500">No deals found.</p>;
  return (
    <ul className="mt-2 space-y-1">
      {deals.map((d) => (
        <li key={d.id} className="flex items-center justify-between rounded border border-[#ece8dd] bg-white px-3 py-2 text-sm">
          <span className="font-medium text-[#0F172A]">{d.name}</span>
          <span className="text-xs text-[#64748B] capitalize">{d.status}</span>
        </li>
      ))}
    </ul>
  );
}

function InvestmentCard({ investments = [] }) {
  if (!investments.length) return <p className="text-sm text-slate-500">No investments found.</p>;
  return (
    <ul className="mt-2 space-y-2">
      {investments.map((inv) => (
        <li key={inv.id} className="rounded border border-[#ece8dd] bg-white px-3 py-2 text-sm">
          <p className="font-medium text-[#0F172A]">{inv.deal_name}</p>
          <p className="text-xs text-[#64748B]">
            {inv.current_stage} · {inv.status}
            {inv.committed_amount ? ` · ${inv.currency ?? ""} ${Number(inv.committed_amount).toLocaleString()}` : ""}
          </p>
        </li>
      ))}
    </ul>
  );
}

function NoteDraft({ note }) {
  if (!note) return null;
  return (
    <div className="mt-2 rounded border border-[#ece8dd] bg-white px-3 py-2 text-sm text-[#334155]">
      <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-[#C5A880]">Note</p>
      <p className="whitespace-pre-wrap">{note.note_text}</p>
    </div>
  );
}

function ToDoList({ todos = [] }) {
  const STATUS_LABEL = {
    awaiting_review: "Awaiting",
    in_progress: "In progress",
    blocked: "Blocked",
    unread: "Unread",
  };
  if (!todos.length) return <p className="text-sm text-slate-500">Nothing pending.</p>;
  return (
    <ul className="mt-2 space-y-1">
      {todos.map((t) => (
        <li key={t.id} className="flex items-center justify-between rounded border border-[#ece8dd] bg-white px-3 py-2 text-sm">
          <span className="text-[#0F172A]">{t.label}</span>
          <span className="rounded-full bg-[#F5F1EB] px-2 py-0.5 text-xs text-[#64748B]">
            {STATUS_LABEL[t.status] ?? t.status}
          </span>
        </li>
      ))}
    </ul>
  );
}

const COMPONENT_MAP = {
  DealList,
  InvestmentCard,
  NoteDraft,
  ToDoList,
  SPVList,
  CapTable,
};

export default function RenderDirective({ render, onNavigate }) {
  if (!render) return null;
  const { component, props = {}, target, screen_route } = render;

  if (target === "screen" && screen_route && onNavigate) {
    onNavigate(screen_route);
    return (
      <p className="mt-1 text-xs text-[#64748B]">
        Opened {component} for you →
      </p>
    );
  }

  const Comp = COMPONENT_MAP[component];
  if (!Comp) {
    return (
      <p className="mt-1 text-xs text-[#64748B]">[{component}]</p>
    );
  }
  return <Comp {...props} />;
}
