"use client";

import { useActionState, useEffect, useRef, useState } from "react";
import { addEmploymentAction, searchEntitiesAction } from "@/lib/crmActions";

const INPUT = "rounded-md border border-border bg-bg-card px-3 py-2 text-sm text-text-primary outline-none focus:ring-2 focus:ring-navy";

function EmployerTypeahead({ excludeId }) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState([]);
  const [selected, setSelected] = useState(null);
  const timer = useRef(null);

  function onChange(value) {
    setQuery(value);
    setSelected(null);
    clearTimeout(timer.current);
    if (!value.trim()) {
      setResults([]);
      return;
    }
    timer.current = setTimeout(async () => {
      const res = await searchEntitiesAction(value);
      setResults((res.results || []).filter((e) => e.id !== excludeId));
    }, 300);
  }

  return (
    <div className="sm:col-span-2">
      <label className="block text-xs font-medium uppercase tracking-wide text-text-muted">
        Employer (search entities)
      </label>
      <input type="hidden" name="employer_id" value={selected?.id || ""} />
      <input
        value={selected ? selected.display_name : query}
        onChange={(e) => onChange(e.target.value)}
        placeholder="Type to search…"
        className={`mt-1 w-full ${INPUT}`}
      />
      {!selected && results.length > 0 && (
        <ul className="mt-1 max-h-40 overflow-auto rounded-md border border-border bg-bg-card">
          {results.map((e) => (
            <li key={e.id}>
              <button
                type="button"
                onClick={() => {
                  setSelected(e);
                  setResults([]);
                }}
                className="block w-full px-3 py-2 text-left text-sm text-text-primary hover:bg-bg-app"
              >
                {e.display_name}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default function EmploymentTab({ entityId, initial }) {
  const [items, setItems] = useState(initial || []);
  const [adding, setAdding] = useState(false);
  const formRef = useRef(null);
  const [state, formAction, pending] = useActionState(
    addEmploymentAction.bind(null, entityId),
    {},
  );

  useEffect(() => {
    if (state?.ok && state.item) {
      setItems((prev) =>
        prev.some((e) => e.id === state.item.id) ? prev : [...prev, state.item],
      );
      formRef.current?.reset();
      setAdding(false);
    }
  }, [state]);

  return (
    <div>
      <div className="flex items-center justify-between">
        <h2 className="text-base font-semibold text-navy">Employment</h2>
        {!adding && (
          <button type="button" onClick={() => setAdding(true)} className="text-sm font-medium text-navy hover:underline">
            Add employment
          </button>
        )}
      </div>

      {items.length === 0 ? (
        <p className="mt-3 text-sm text-text-muted">No employment records.</p>
      ) : (
        <ul className="mt-3 space-y-2">
          {items.map((e) => (
            <li key={e.id} className="rounded-lg border border-border bg-bg-card p-4">
              <div className="flex items-center justify-between">
                <a href={`/crm/${e.employer_id}`} className="text-sm font-medium text-navy hover:underline">
                  {e.employer_name || "Employer"}
                </a>
                {e.is_current && (
                  <span className="inline-flex items-center rounded-full bg-gold-light px-2.5 py-0.5 text-xs font-medium text-navy">
                    Current
                  </span>
                )}
              </div>
              <p className="mt-1 text-sm text-text-secondary">{e.title || "—"}</p>
              <p className="mt-0.5 text-xs text-text-muted">
                {[e.start_date, e.end_date].filter(Boolean).join(" – ") || "Dates not set"}
              </p>
            </li>
          ))}
        </ul>
      )}

      {adding && (
        <form ref={formRef} action={formAction} className="mt-4 grid max-w-xl gap-3 sm:grid-cols-2">
          <EmployerTypeahead excludeId={entityId} />
          <input name="title" placeholder="Title" className={INPUT} />
          <div />
          <input type="date" name="start_date" className={INPUT} />
          <input type="date" name="end_date" className={INPUT} />
          <label className="flex items-center gap-2 text-sm text-text-secondary">
            <input type="checkbox" name="is_current" /> Current role
          </label>
          <input name="notes" placeholder="Notes" className={`${INPUT} sm:col-span-2`} />
          {state?.error && <p className="text-sm text-[#9B2335] sm:col-span-2">{state.error}</p>}
          <div className="flex gap-2 sm:col-span-2">
            <button type="submit" disabled={pending} className="rounded-md bg-navy px-4 py-2 text-sm font-medium text-bg-app hover:opacity-90 disabled:opacity-60">
              {pending ? "Saving…" : "Save employment"}
            </button>
            <button type="button" onClick={() => setAdding(false)} className="rounded-md border border-border px-4 py-2 text-sm font-medium text-text-secondary hover:bg-border">
              Cancel
            </button>
          </div>
        </form>
      )}
    </div>
  );
}
