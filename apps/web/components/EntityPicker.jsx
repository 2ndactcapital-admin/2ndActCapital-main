"use client";

import { useEffect, useRef, useState } from "react";

const INPUT =
  "w-full rounded-md border border-border bg-bg-card px-3 py-2 text-sm text-text-primary outline-none focus:ring-2 focus:ring-navy";

const TYPE_LABELS = {
  individual: "Person",
  trust: "Trust",
  llc: "LLC",
  lp: "LP",
  gp: "GP",
  s_corp: "S-Corp",
  c_corp: "C-Corp",
  corporation: "Corp",
  foundation: "Foundation",
  family_office: "Family Office",
  household: "Household",
  other: "Other",
};

async function apiSearch({ q, entityTypes, excludeId, page, pageSize }) {
  const p = new URLSearchParams();
  if (q) p.set("q", q);
  (entityTypes || []).forEach((t) => p.append("entity_type", t));
  if (excludeId) p.append("exclude_ids", excludeId);
  p.set("page", String(page || 1));
  p.set("page_size", String(pageSize || 20));
  const res = await fetch(`/api/entities/search?${p}`, { cache: "no-store" });
  if (!res.ok) return { items: [], total: 0, has_more: false };
  return res.json();
}

async function apiStub({ display_name, entity_type, force_create }) {
  const res = await fetch("/api/entities/stub", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ display_name, entity_type, force_create: !!force_create }),
  });
  return { status: res.status, data: await res.json() };
}

/**
 * Reusable entity picker with debounced search, type badges, incomplete
 * chips, pagination, and optional allowCreate flow with dupe-check confirm.
 *
 * Props:
 *   value           — { id, display_name } | null
 *   onChange        — (entity | null) => void
 *   placeholder     — string
 *   entityTypes     — string[] — filter results to these entity_type values
 *   allowCreate     — bool — show "+ Create" option and POST /entities/stub
 *   createEntityType — string — entity_type to use when creating a stub
 *   excludeId       — uuid string to exclude from results (e.g. current entity)
 *   className       — override input class
 */
export default function EntityPicker({
  value,
  onChange,
  placeholder = "Search entities…",
  entityTypes = [],
  allowCreate = false,
  createEntityType = "individual",
  excludeId = null,
  className,
}) {
  const [query, setQuery] = useState(value?.display_name || "");
  const [results, setResults] = useState([]);
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  const [page, setPage] = useState(1);
  const [confirmDupes, setConfirmDupes] = useState(null);
  const timer = useRef(null);
  const wrapRef = useRef(null);

  // Sync external value changes
  useEffect(() => {
    setQuery(value?.display_name || "");
  }, [value?.id]);

  // Close dropdown on outside click
  useEffect(() => {
    function onDown(e) {
      if (wrapRef.current && !wrapRef.current.contains(e.target)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, []);

  function runSearch(val, pg = 1) {
    setLoading(true);
    clearTimeout(timer.current);
    timer.current = setTimeout(async () => {
      const data = await apiSearch({ q: val, entityTypes, excludeId, page: pg, pageSize: 20 });
      if (pg === 1) {
        setResults(data.items || []);
      } else {
        setResults((prev) => [...prev, ...(data.items || [])]);
      }
      setHasMore(data.has_more || false);
      setPage(pg);
      setLoading(false);
    }, 300);
  }

  function handleInput(val) {
    setQuery(val);
    onChange(null);
    setPage(1);
    if (!val.trim()) {
      setResults([]);
      setOpen(false);
      return;
    }
    setOpen(true);
    runSearch(val, 1);
  }

  function loadMore() {
    runSearch(query, page + 1);
  }

  function select(entity) {
    setQuery(entity.display_name);
    setResults([]);
    setOpen(false);
    onChange(entity);
  }

  async function handleCreate() {
    const { status, data } = await apiStub({
      display_name: query.trim(),
      entity_type: createEntityType,
    });
    if (status === 409 && data.possible_duplicates) {
      setOpen(false);
      setConfirmDupes({
        display_name: query.trim(),
        entity_type: createEntityType,
        dupes: data.possible_duplicates,
      });
      return;
    }
    if (status === 201) {
      select({ id: data.id, display_name: data.display_name });
    }
  }

  async function forceCreate() {
    const { display_name, entity_type } = confirmDupes;
    setConfirmDupes(null);
    const { status, data } = await apiStub({ display_name, entity_type, force_create: true });
    if (status === 201) {
      select({ id: data.id, display_name: data.display_name });
    }
  }

  return (
    <div ref={wrapRef} className="relative">
      <input
        type="text"
        value={query}
        onChange={(e) => handleInput(e.target.value)}
        onFocus={() => {
          if (query.trim() && !value) {
            setOpen(true);
            if (results.length === 0) runSearch(query, 1);
          }
        }}
        placeholder={placeholder}
        className={className || INPUT}
        autoComplete="off"
      />

      {open && (
        <div className="absolute z-50 mt-1 max-h-64 w-full overflow-auto rounded-md border border-border bg-white shadow-sm">
          {loading && (
            <p className="px-3 py-2 text-sm text-text-muted">Searching…</p>
          )}
          {!loading && results.length === 0 && (
            <p className="px-3 py-2 text-sm text-text-muted">No results.</p>
          )}
          {results.map((e) => (
            <button
              key={e.id}
              type="button"
              onMouseDown={(ev) => ev.preventDefault()}
              onClick={() => select(e)}
              className="flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-sm text-text-primary hover:bg-bg-app"
            >
              <span className="flex items-center gap-1.5 truncate">
                <span className="truncate">{e.display_name}</span>
                {e.is_incomplete && (
                  <span
                    className="shrink-0 rounded-full px-1.5 py-0.5 text-[10px] font-medium"
                    style={{ backgroundColor: "#E8D5A3", color: "#1B2B4B" }}
                  >
                    Incomplete
                  </span>
                )}
              </span>
              <span className="shrink-0 text-xs text-text-muted">
                {TYPE_LABELS[e.entity_type] || e.entity_type}
              </span>
            </button>
          ))}
          {hasMore && !loading && (
            <button
              type="button"
              onMouseDown={(e) => e.preventDefault()}
              onClick={loadMore}
              className="w-full px-3 py-1.5 text-center text-xs text-navy hover:bg-bg-app"
            >
              Load more
            </button>
          )}
          {allowCreate && query.trim() && !loading && (
            <button
              type="button"
              onMouseDown={(e) => { e.preventDefault(); handleCreate(); }}
              className="w-full border-t border-border px-3 py-2 text-left text-sm font-medium text-navy hover:bg-bg-app"
            >
              + Create &ldquo;{query.trim()}&rdquo;
            </button>
          )}
        </div>
      )}

      {/* Duplicate-confirmation modal */}
      {confirmDupes && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30">
          <div className="w-full max-w-md rounded-lg border border-border bg-white p-6 shadow-lg">
            <h3 className="font-semibold text-navy" style={{ fontFamily: "Spectral, Georgia, serif" }}>
              Similar entities found
            </h3>
            <p className="mt-1 text-sm text-text-secondary">
              These entities have similar names. Select one, or create a new record.
            </p>
            <ul className="mt-3 space-y-1">
              {confirmDupes.dupes.map((d) => (
                <li key={d.id}>
                  <button
                    type="button"
                    onClick={() => {
                      setConfirmDupes(null);
                      select(d);
                    }}
                    className="flex w-full items-center justify-between rounded px-3 py-2 text-left text-sm text-text-primary hover:bg-bg-app"
                  >
                    <span>{d.display_name}</span>
                    <span className="text-xs text-text-muted">
                      {TYPE_LABELS[d.entity_type] || d.entity_type}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
            <div className="mt-4 flex gap-2">
              <button
                type="button"
                onClick={forceCreate}
                className="rounded-md px-4 py-2 text-sm font-medium text-white hover:opacity-90"
                style={{ backgroundColor: "#1B2B4B" }}
              >
                Create anyway
              </button>
              <button
                type="button"
                onClick={() => setConfirmDupes(null)}
                className="rounded-md border border-border px-4 py-2 text-sm font-medium text-text-secondary hover:bg-bg-app"
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
