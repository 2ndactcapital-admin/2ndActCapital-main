"use client";

import { Suspense, useEffect, useRef, useState } from "react";
import { IconSearch } from "@tabler/icons-react";
import { usePermissions } from "@/lib/usePermissions";
import { useEntityContext, rememberEntity } from "@/lib/useEntityContext";
import { openAssistantWith } from "@/lib/useAssistantPanel";
import { isOpenShortcut, paletteOptions } from "@/lib/commandPalette.mjs";
import { entityDestination } from "@/lib/entityScreens.mjs";
import { searchEntities, moveIndex, StatusFilterSelect, useStatusOptions } from "@/components/nav/EntityChip";

// NAV2 — Ctrl K (Cmd K on a Mac) and the top-bar search button open ONE dialog: Records (entities + securities,
// labelled), Screens (the person's visible menu — lib/commandPalette.screensFor) and "Ask about <entity>…".
// Esc closes and returns focus; arrows + Enter choose; Tab stays inside. The global shortcut ignores keys typed into
// inputs, textareas, selects and editable regions.

async function searchSecurities(q, { signal } = {}) {
  const params = new URLSearchParams({ q });
  const res = await fetch(`/api/market/securities/search?${params}`, { cache: "no-store", signal });
  if (!res.ok) return [];
  const data = await res.json().catch(() => ({}));
  return (data.securities || []).slice(0, 5).map((s) => ({ id: String(s.id), name: s.name, ticker: s.ticker || null }));
}

/** Pure presentation over the option list — exported for node tests. */
export function PaletteView({ query, onQuery, options, activeIndex, onChoose, inputRef, searching, statusOptions, status, onStatus }) {
  let lastSection = null;
  return (
    <div className="p-3">
      <label htmlFor="nav2-cmdk-input" className="sr-only">Find a record or screen, or ask</label>
      <input
        id="nav2-cmdk-input"
        ref={inputRef}
        type="search"
        value={query}
        onChange={(e) => onQuery(e.target.value)}
        placeholder="Find a household, client, account, security or screen…"
        role="combobox"
        aria-expanded="true"
        aria-controls="nav2-cmdk-list"
        aria-activedescendant={activeIndex >= 0 && options[activeIndex] ? `nav2-cmdk-opt-${activeIndex}` : undefined}
        autoComplete="off"
        className="w-full rounded-md border border-border bg-white px-3 py-2 text-base text-text-primary focus:outline-none focus:ring-1 focus:ring-gold"
      />
      <StatusFilterSelect id="nav3-cmdk-status" options={statusOptions} value={status} onChange={onStatus} />
      <ul id="nav2-cmdk-list" role="listbox" aria-label="Results" className="mt-2 max-h-96 overflow-y-auto">
        {options.map((o, i) => {
          const header = o.section !== lastSection ? o.section : null;
          lastSection = o.section;
          return [
            header && (
              <li key={`h:${header}`} role="presentation"
                className="px-2 pt-3 pb-1 text-[11px] font-semibold uppercase tracking-wider text-text-muted">
                {header}
              </li>
            ),
            <li
              key={o.key}
              id={`nav2-cmdk-opt-${i}`}
              role="option"
              aria-selected={i === activeIndex}
              data-kind={o.kind}
              data-href={o.href || undefined}
              onMouseDown={(e) => { e.preventDefault(); onChoose(o); }}
              className={`flex cursor-pointer items-center justify-between rounded-md px-2 py-1.5 text-sm ${i === activeIndex ? "bg-bg-sidebar" : ""}`}
            >
              <span className="truncate text-text-primary">{o.label}</span>
              {o.detail && <span className="ml-2 shrink-0 text-xs text-text-muted">{o.detail}</span>}
            </li>,
          ];
        })}
        {options.length === 0 && (
          <li role="presentation" className="px-2 py-2 text-sm text-text-muted">
            {searching ? "Searching…" : "Nothing you can open matches."}
          </li>
        )}
      </ul>
    </div>
  );
}

function PaletteInner() {
  const { me } = usePermissions();
  const ctx = useEntityContext();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [entities, setEntities] = useState([]);
  const [securities, setSecurities] = useState([]);
  const [searching, setSearching] = useState(false);
  const [active, setActive] = useState(0);
  const [status, setStatus] = useState("");   // NAV3 — relationship-status filter on Records (entities)
  const statusOpts = useStatusOptions(open);
  const inputRef = useRef(null);
  const dialogRef = useRef(null);
  const returnFocus = useRef(null);

  function show() {
    returnFocus.current = typeof document !== "undefined" ? document.activeElement : null;
    setOpen(true);
  }
  function hide() {
    setOpen(false);
    setQuery("");
    setEntities([]);
    setSecurities([]);
    setActive(0);
    const el = returnFocus.current;
    if (el && typeof el.focus === "function") setTimeout(() => el.focus(), 0);
  }

  useEffect(() => {
    function onKey(e) {
      if (isOpenShortcut(e)) {
        e.preventDefault();
        if (!open) show();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  useEffect(() => {
    const q = query.trim();
    if (!open || q.length < 2) { setEntities([]); setSecurities([]); setSearching(false); return; }
    const ctrl = new AbortController();
    setSearching(true);
    const t = setTimeout(() => {
      Promise.all([
        searchEntities(q, { signal: ctrl.signal, status }).catch(() => []),
        searchSecurities(q, { signal: ctrl.signal }).catch(() => []),
      ]).then(([e, s]) => { setEntities(e.slice(0, 8)); setSecurities(s); setActive(0); })
        .finally(() => setSearching(false));
    }, 250);
    return () => { clearTimeout(t); ctrl.abort(); };
  }, [query, open, status]);

  const options = open
    ? paletteOptions({ me, query, entities, securities, selected: ctx.state, carryId: ctx.carryId })
    : [];

  function choose(o) {
    if (!o) return;
    if (o.kind === "ask") {
      hide();
      openAssistantWith(`About ${o.entity.display_name}: `);
      return;
    }
    if (o.kind === "entity") {
      if (ctx.userId) rememberEntity(ctx.userId, o.entity);
      setOpen(false);
      window.location.assign(entityDestination({
        me, pathname: window.location.pathname, search: window.location.search, entityId: o.entity.id,
      }));
      return;
    }
    setOpen(false);
    window.location.assign(o.href);
  }

  function onKeyDown(e) {
    if (e.key === "Escape") { e.preventDefault(); hide(); return; }
    if (e.key === "Enter") { e.preventDefault(); choose(options[active]); return; }
    if (["ArrowDown", "ArrowUp", "Home", "End"].includes(e.key)) {
      e.preventDefault();
      setActive((i) => moveIndex(e.key, i, options.length));
      return;
    }
    if (e.key === "Tab") {
      // the input is the only focusable control inside: keep focus there (focus trap)
      e.preventDefault();
      inputRef.current?.focus();
    }
  }

  return (
    <>
      <button
        type="button"
        onClick={show}
        aria-haspopup="dialog"
        aria-keyshortcuts="Control+K Meta+K"
        data-testid="cmdk-button"
        className="flex items-center gap-1.5 rounded-md px-2 py-1 text-sm text-bg-app transition-opacity hover:opacity-80"
      >
        <IconSearch size={16} stroke={1.8} aria-hidden="true" />
        <span>Search</span>
        <kbd className="ml-1 rounded border px-1 text-[10px]" style={{ borderColor: "color-mix(in srgb, var(--2a-gold-light) 40%, transparent)" }}>Ctrl K</kbd>
      </button>
      {open && (
        <div className="fixed inset-0 z-[60] flex items-start justify-center pt-24" style={{ background: "rgba(15,23,42,0.25)" }}
          onMouseDown={(e) => { if (e.target === e.currentTarget) hide(); }}>
          <div
            ref={dialogRef}
            role="dialog"
            aria-modal="true"
            aria-label="Find or ask"
            data-testid="cmdk-dialog"
            onKeyDown={onKeyDown}
            className="w-[36rem] max-w-[92vw] rounded-lg border border-border bg-bg-card"
            style={{ boxShadow: "0 1px 3px rgba(0,0,0,0.06)" }}
          >
            <PaletteView
              query={query}
              onQuery={(v) => { setQuery(v); setActive(0); }}
              options={options}
              activeIndex={active}
              onChoose={choose}
              inputRef={inputRef}
              searching={searching}
              statusOptions={statusOpts}
              status={status}
              onStatus={(v) => { setStatus(v); setActive(0); }}
            />
          </div>
        </div>
      )}
    </>
  );
}

export default function CommandPalette() {
  return (
    <Suspense fallback={null}>
      <PaletteInner />
    </Suspense>
  );
}
