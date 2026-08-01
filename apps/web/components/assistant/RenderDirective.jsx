"use client";

import SPVList from "./render/SPVList";
import CapTable from "./render/CapTable";
import SPVLedger from "./render/SPVLedger";
import EntityTree from "./render/EntityTree";
import AllocationSunburst from "./render/AllocationSunburst";

// Maps render.component string → React component.
// Unknown components fall back to a plain text display.

function DealList({ deals = [] }) {
  if (!deals.length) return <p className="text-sm text-slate-500">No deals found.</p>;
  return (
    <ul className="mt-2 space-y-1">
      {deals.map((d) => (
        <li key={d.id} className="flex items-center justify-between rounded border border-[#ece8dd] bg-white px-3 py-2 text-sm">
          <span className="font-medium text-[var(--2a-text)]">{d.name}</span>
          <span className="text-xs text-[var(--2a-text-muted)] capitalize">{d.status}</span>
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
          <p className="font-medium text-[var(--2a-text)]">{inv.deal_name}</p>
          <p className="text-xs text-[var(--2a-text-muted)]">
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
    <div className="mt-2 rounded border border-[#ece8dd] bg-white px-3 py-2 text-sm text-[var(--2a-text-secondary)]">
      <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-[var(--2a-gold)]">Note</p>
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
          <span className="text-[var(--2a-text)]">{t.label}</span>
          <span className="rounded-full bg-[var(--2a-bg-sidebar)] px-2 py-0.5 text-xs text-[var(--2a-text-muted)]">
            {STATUS_LABEL[t.status] ?? t.status}
          </span>
        </li>
      ))}
    </ul>
  );
}

function CountCard({ count = 0, noun = "result", plural, filters = {}, rows = [], renderRow }) {
  const chips = Object.entries(filters).filter(([, v]) => v);
  return (
    <div className="mt-2 rounded border border-[#ece8dd] bg-white px-3 py-2">
      <p className="text-sm">
        <span className="text-2xl font-semibold text-[var(--2a-navy)]">{count}</span>
        <span className="ml-1 text-[var(--2a-text-secondary)]">
          {count === 1 ? noun : (plural || `${noun}s`)}
        </span>
      </p>
      {chips.length > 0 && (
        <div className="mt-1 flex flex-wrap gap-1">
          {chips.map(([k, v]) => (
            <span key={k} className="rounded-full bg-[var(--2a-bg-sidebar)] px-2 py-0.5 text-xs text-[var(--2a-text-muted)]">
              {k}: {String(v)}
            </span>
          ))}
        </div>
      )}
      {rows.length > 0 && (
        <ul className="mt-2 space-y-1">{rows.map(renderRow)}</ul>
      )}
    </div>
  );
}

function EntityCount({ count = 0, entities = [], filters = {} }) {
  return (
    <CountCard
      count={count}
      noun="entity"
      plural="entities"
      filters={filters}
      rows={entities}
      renderRow={(e) => (
        <li key={e.id} className="flex items-center justify-between rounded border border-[#ece8dd] px-3 py-1.5 text-sm">
          <span className="text-[var(--2a-text)]">{e.display_name}</span>
          <span className="text-xs capitalize text-[var(--2a-text-muted)]">{e.entity_type}</span>
        </li>
      )}
    />
  );
}

function InvestmentCount({ count = 0, investments = [], filters = {} }) {
  return (
    <CountCard
      count={count}
      noun="investment"
      filters={filters}
      rows={investments}
      renderRow={(inv) => (
        <li key={inv.id} className="flex items-center justify-between rounded border border-[#ece8dd] px-3 py-1.5 text-sm">
          <span className="text-[var(--2a-text)]">{inv.deal_name}</span>
          <span className="text-xs text-[var(--2a-text-muted)]">{inv.investment_stage}</span>
        </li>
      )}
    />
  );
}

const COMPONENT_MAP = {
  DealList,
  InvestmentCard,
  NoteDraft,
  ToDoList,
  EntityCount,
  InvestmentCount,
  SPVList,
  CapTable,
  SPVLedger,
  EntityTree,
  AllocationSunburst,
};

export default function RenderDirective({ render, onNavigate }) {
  if (!render) return null;
  const { component, props = {}, target, screen_route } = render;

  if (target === "screen" && screen_route && onNavigate) {
    onNavigate(screen_route);
    return (
      <p className="mt-1 text-xs text-[var(--2a-text-muted)]">
        Opened {component} for you →
      </p>
    );
  }

  const Comp = COMPONENT_MAP[component];
  if (!Comp) {
    return (
      <p className="mt-1 text-xs text-[var(--2a-text-muted)]">[{component}]</p>
    );
  }
  return <Comp {...props} />;
}
