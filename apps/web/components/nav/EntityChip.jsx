"use client";

import { Suspense, useEffect, useRef, useState } from "react";
import { usePathname } from "next/navigation";
import { IconChevronDown, IconX } from "@tabler/icons-react";
import { useEntityContext, forgetEntity, rememberEntity } from "@/lib/useEntityContext";
import { usePermissions } from "@/lib/usePermissions";
import { NOT_AVAILABLE, typeLabel, withEntity } from "@/lib/entityContext.mjs";
import { entityDestination } from "@/lib/entityScreens.mjs";

// NAV2 — the top-bar entity chip: type + name; a picker with search (GET /api/entities/search → the existing
// /api/v1/entities/search, member + staff visibility applied in SQL) and Recent (last 8 opened, this browser).
// Choosing navigates (same screen with entity_id when the screen has an entity view, else the entity's own page);
// clearing drops entity_id. A forbidden or missing id reads "Not available" — no name, ever.

/** The chip button's text + label, from chipState(). Exported for node tests. */
export function chipModel(state) {
  if (state.kind === "entity") {
    return { text: state.name, type: typeLabel(state.type), aria: `Selected ${typeLabel(state.type) || "entity"}: ${state.name}. Change`, set: true };
  }
  if (state.kind === "unavailable") return { text: NOT_AVAILABLE, type: "", aria: `Selected entity: ${NOT_AVAILABLE}. Change`, set: true };
  if (state.kind === "loading") return { text: "Loading…", type: "", aria: "Selected entity loading. Change", set: true };
  return { text: "All entities", type: "", aria: "No entity selected. Choose an entity", set: false };
}

export function ChipButton({ state, open, onClick }) {
  const m = chipModel(state);
  return (
    <button
      type="button"
      onClick={onClick}
      aria-haspopup="dialog"
      aria-expanded={open}
      aria-label={m.aria}
      data-testid="entity-chip"
      data-chip-kind={state.kind}
      className="flex max-w-[22rem] items-center gap-2 rounded-md border px-2.5 py-1 text-sm text-bg-app transition-opacity hover:opacity-90"
      style={{ borderColor: "color-mix(in srgb, var(--2a-gold-light) 40%, transparent)" }}
    >
      {m.type && (
        <span className="rounded px-1.5 py-0.5 text-[11px] font-semibold uppercase tracking-wide"
          style={{ background: "color-mix(in srgb, var(--2a-gold-light) 18%, transparent)", color: "var(--2a-gold-light)" }}>
          {m.type}
        </span>
      )}
      <span className="truncate">{m.text}</span>
      <IconChevronDown size={14} stroke={2} aria-hidden="true" />
    </button>
  );
}

/** The picker body — pure over its props (results / recent fed in) so node tests render it. */
export function EntityPickerView({ query, onQuery, results, recent, searching, activeIndex, onChoose, onClear, hasSelection, inputRef,
  statusOptions, status, onStatus }) {
  const list = query.trim() ? results : recent;
  const heading = query.trim() ? "Results" : "Recent";
  return (
    <div className="p-3">
      <label htmlFor="nav2-entity-search" className="mb-1 block text-xs font-medium text-text-muted">
        Search households, clients, accounts and legal entities
      </label>
      <input
        id="nav2-entity-search"
        ref={inputRef}
        type="search"
        value={query}
        onChange={(e) => onQuery(e.target.value)}
        role="combobox"
        aria-expanded="true"
        aria-controls="nav2-entity-list"
        aria-activedescendant={activeIndex >= 0 && list[activeIndex] ? `nav2-entity-opt-${activeIndex}` : undefined}
        autoComplete="off"
        className="w-full rounded-md border border-border bg-white px-3 py-2 text-sm text-text-primary focus:outline-none focus:ring-1 focus:ring-gold"
      />
      <StatusFilterSelect id="nav3-entity-status" options={statusOptions} value={status} onChange={onStatus} />
      <p className="mt-3 mb-1 text-[11px] font-semibold uppercase tracking-wider text-text-muted" id="nav2-entity-heading">
        {heading}
      </p>
      <ul id="nav2-entity-list" role="listbox" aria-labelledby="nav2-entity-heading" className="max-h-72 overflow-y-auto">
        {list.map((r, i) => (
          <li
            key={r.id}
            id={`nav2-entity-opt-${i}`}
            role="option"
            aria-selected={i === activeIndex}
            data-entity-id={r.id}
            onMouseDown={(e) => { e.preventDefault(); onChoose(r); }}
            className={`flex cursor-pointer items-center justify-between rounded-md px-2 py-1.5 text-sm ${i === activeIndex ? "bg-bg-sidebar" : ""}`}
          >
            <span className="truncate text-text-primary">{r.display_name}</span>
            <span className="ml-2 shrink-0 text-xs text-text-muted">{typeLabel(r.entity_type)}</span>
          </li>
        ))}
        {list.length === 0 && (
          <li className="px-2 py-1.5 text-sm text-text-muted" role="presentation">
            {query.trim() ? (searching ? "Searching…" : "Nothing you can see matches.") : "Nothing opened yet."}
          </li>
        )}
      </ul>
      {hasSelection && (
        <button type="button" onClick={onClear} data-testid="entity-chip-clear"
          className="mt-3 flex items-center gap-1.5 rounded-md px-2 py-1 text-sm text-text-secondary hover:bg-bg-sidebar">
          <IconX size={14} stroke={2} aria-hidden="true" /> Clear — see everything you can see
        </button>
      )}
    </div>
  );
}

/** Entity search through the existing route (debounced). Returns items reduced to id / name / type. */
export async function searchEntities(q, { signal, status } = {}) {
  const params = new URLSearchParams({ q, page_size: "10", include_operational: "true" });
  if (status) params.set("status", status);   // NAV3 — relationship-status filter (server-side, narrowing)
  const res = await fetch(`/api/entities/search?${params}`, { cache: "no-store", signal });
  if (!res.ok) return [];
  const data = await res.json().catch(() => ({}));
  return (data.items || []).map((r) => ({ id: String(r.id), display_name: r.display_name, entity_type: r.entity_type,
    status: r.status ?? null }));
}

/** NAV3 — the relationship-status filter (labels from the CRM vocabulary envelope; hidden when there is none). */
export function StatusFilterSelect({ id, options, value, onChange }) {
  if (!Array.isArray(options) || options.length < 2) return null;
  return (
    <span className="mt-2 flex items-center gap-2 text-xs text-text-muted">
      <label htmlFor={id}>Relationship</label>
      <select id={id} value={value || ""} onChange={(e) => onChange && onChange(e.target.value)}
        className="rounded-md border border-border bg-white px-2 py-1 text-xs text-text-primary">
        {options.map((o) => <option key={o.value || "all"} value={o.value}>{o.label}</option>)}
      </select>
    </span>
  );
}

/** NAV3 — the status filter options, read once the picker / palette opens (GET /api/crm/vocabularies, internal).
 * A refused or failed read → null → no filter shown (never a client-side default list). */
let _statusCache = null;
export function useStatusOptions(open) {
  const [opts, setOpts] = useState(_statusCache);
  useEffect(() => {
    if (!open || _statusCache) return undefined;
    let live = true;
    fetch("/api/crm/vocabularies", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        const list = d && d.vocabularies && Array.isArray(d.vocabularies.status_filters) ? d.vocabularies.status_filters : null;
        _statusCache = list;
        if (live) setOpts(list);
      })
      .catch(() => {});
    return () => { live = false; };
  }, [open]);
  return opts;
}

/** Keyboard model shared by the picker and Ctrl K: arrows move, Enter chooses, Esc closes. */
export function moveIndex(key, index, length) {
  if (!length) return -1;
  if (key === "ArrowDown") return index < length - 1 ? index + 1 : 0;
  if (key === "ArrowUp") return index > 0 ? index - 1 : length - 1;
  if (key === "Home") return 0;
  if (key === "End") return length - 1;
  return index;
}

function EntityChipInner() {
  const ctx = useEntityContext();
  const { me } = usePermissions();
  const pathname = usePathname() || "/";
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState([]);
  const [searching, setSearching] = useState(false);
  const [active, setActive] = useState(-1);
  const [status, setStatus] = useState("");
  const statusOpts = useStatusOptions(open);
  const wrapRef = useRef(null);
  const buttonRef = useRef(null);
  const inputRef = useRef(null);

  useEffect(() => {
    if (!open) return;
    inputRef.current?.focus();
    function outside(e) {
      if (wrapRef.current && !wrapRef.current.contains(e.target)) setOpen(false);
    }
    document.addEventListener("mousedown", outside);
    return () => document.removeEventListener("mousedown", outside);
  }, [open]);

  useEffect(() => {
    const q = query.trim();
    if (!q) { setResults([]); setSearching(false); return; }
    const ctrl = new AbortController();
    setSearching(true);
    const t = setTimeout(() => {
      searchEntities(q, { signal: ctrl.signal, status })
        .then((r) => { setResults(r); setActive(r.length ? 0 : -1); })
        .catch(() => {})
        .finally(() => setSearching(false));
    }, 250);
    return () => { clearTimeout(t); ctrl.abort(); };
  }, [query, status]);

  const list = query.trim() ? results : ctx.recent;

  function close() {
    setOpen(false);
    setQuery("");
    setActive(-1);
    buttonRef.current?.querySelector("button")?.focus();
  }

  function choose(r) {
    if (ctx.userId) rememberEntity(ctx.userId, r);
    const search = typeof window !== "undefined" ? window.location.search : "";
    const href = entityDestination({ me, pathname, search, entityId: r.id });
    setOpen(false);
    window.location.assign(href);
  }

  function clear() {
    if (ctx.userId) forgetEntity(ctx.userId);
    setOpen(false);
    window.location.assign(withEntity(`${pathname}${window.location.search}`, null));
  }

  function onKeyDown(e) {
    if (e.key === "Escape") { e.preventDefault(); close(); return; }
    if (e.key === "Enter") {
      if (active >= 0 && list[active]) { e.preventDefault(); choose(list[active]); }
      return;
    }
    if (["ArrowDown", "ArrowUp", "Home", "End"].includes(e.key)) {
      e.preventDefault();
      setActive((i) => moveIndex(e.key, i, list.length));
    }
    if (e.key === "Tab") {
      // keep focus inside the picker while it is open
      const nodes = wrapRef.current?.querySelectorAll('[role="dialog"] input, [role="dialog"] button');
      if (nodes && nodes.length) {
        const first = nodes[0];
        const last = nodes[nodes.length - 1];
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
      }
    }
  }

  return (
    <div className="relative" ref={wrapRef}>
      <span ref={buttonRef}>
        <ChipButton state={ctx.state} open={open} onClick={() => setOpen((v) => !v)} />
      </span>
      {open && (
        <div
          role="dialog"
          aria-modal="false"
          aria-label="Choose an entity"
          onKeyDown={onKeyDown}
          className="absolute left-0 top-full z-50 mt-1 w-96 rounded-lg border border-border bg-bg-card"
          style={{ boxShadow: "0 1px 3px rgba(0,0,0,0.06)" }}
        >
          <EntityPickerView
            query={query}
            onQuery={(v) => { setQuery(v); setActive(-1); }}
            results={results}
            recent={ctx.recent}
            searching={searching}
            activeIndex={active}
            onChoose={choose}
            onClear={clear}
            hasSelection={ctx.state.kind !== "none"}
            inputRef={inputRef}
            statusOptions={statusOpts}
            status={status}
            onStatus={setStatus}
          />
        </div>
      )}
    </div>
  );
}

export default function EntityChip() {
  return (
    <Suspense fallback={<ChipButton state={{ kind: "none" }} open={false} onClick={() => {}} />}>
      <EntityChipInner />
    </Suspense>
  );
}
