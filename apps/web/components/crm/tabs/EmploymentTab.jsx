"use client";

import { useActionState, useEffect, useRef, useState } from "react";
import { addEmploymentAction } from "@/lib/crmActions";
import EntityPicker from "@/components/EntityPicker";

const INPUT = "rounded-md border border-border bg-bg-card px-3 py-2 text-sm text-text-primary outline-none focus:ring-2 focus:ring-navy";

const COMPANY_TYPES = ["llc", "lp", "gp", "s_corp", "c_corp", "corporation", "foundation", "family_office", "other"];

export default function EmploymentTab({ entityId, initial }) {
  const [items, setItems] = useState(initial || []);
  const [adding, setAdding] = useState(false);
  const [selectedEmployer, setSelectedEmployer] = useState(null);
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
      setSelectedEmployer(null);
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
          <div className="sm:col-span-2">
            <label className="block text-xs font-medium uppercase tracking-wide text-text-muted">
              Employer (search entities)
            </label>
            <input type="hidden" name="employer_id" value={selectedEmployer?.id || ""} />
            <EntityPicker
              value={selectedEmployer}
              onChange={setSelectedEmployer}
              placeholder="Search for employer…"
              entityTypes={COMPANY_TYPES}
              allowCreate
              createEntityType="llc"
              excludeId={entityId}
              className={`mt-1 ${INPUT} w-full`}
            />
          </div>
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
            <button type="button" onClick={() => { setAdding(false); setSelectedEmployer(null); }} className="rounded-md border border-border px-4 py-2 text-sm font-medium text-text-secondary hover:bg-border">
              Cancel
            </button>
          </div>
        </form>
      )}
    </div>
  );
}
