"use client";

import { useActionState, useEffect, useRef, useState } from "react";
import { addTaxIdAction } from "@/lib/crmActions";
import { TAX_ID_TYPES, taxIdConfig } from "@/lib/taxIdConfig";

const INPUT = "rounded-md border border-border bg-bg-card px-3 py-2 text-sm text-text-primary outline-none focus:ring-2 focus:ring-navy";

export default function TaxIdsTab({ entityId, initial }) {
  const [items, setItems] = useState(initial || []);
  const [adding, setAdding] = useState(false);
  const [selectedType, setSelectedType] = useState("ssn");
  const formRef = useRef(null);
  const [state, formAction, pending] = useActionState(
    addTaxIdAction.bind(null, entityId),
    {},
  );

  useEffect(() => {
    if (state?.ok && state.item) {
      setItems((prev) =>
        prev.some((t) => t.id === state.item.id) ? prev : [...prev, state.item],
      );
      formRef.current?.reset();
      setSelectedType("ssn");
      setAdding(false);
    }
  }, [state]);

  const cfg = taxIdConfig(selectedType);

  return (
    <div>
      <div className="flex items-center justify-between">
        <h2 className="text-base font-semibold text-navy">Tax IDs</h2>
        {!adding && (
          <button type="button" onClick={() => setAdding(true)} className="text-sm font-medium text-navy hover:underline">
            Add tax ID
          </button>
        )}
      </div>

      {items.length === 0 ? (
        <p className="mt-3 text-sm text-text-muted">No tax IDs on file.</p>
      ) : (
        <ul className="mt-3 divide-y divide-border rounded-lg border border-border bg-bg-card">
          {items.map((t) => (
            <li key={t.id} className="flex items-center justify-between px-4 py-3">
              <div className="flex items-center gap-2">
                <span className="text-sm font-medium text-text-primary">
                  {taxIdConfig(t.tax_id_type).label}
                </span>
                <span className="text-xs text-text-muted">{t.tax_id_country}</span>
                {t.is_primary && <span className="text-xs text-text-muted">· Primary</span>}
              </div>
              <span className="font-mono text-sm text-text-secondary">
                {t.masked || `•••• ${t.tax_id_last4}`}
              </span>
            </li>
          ))}
        </ul>
      )}

      {adding && (
        <form ref={formRef} action={formAction} className="mt-4 grid max-w-xl gap-3 sm:grid-cols-2">
          <div>
            <label className="block text-xs font-medium uppercase tracking-wide text-text-muted">Type</label>
            <select
              name="tax_id_type"
              value={selectedType}
              onChange={(e) => setSelectedType(e.target.value)}
              className={`mt-1 w-full ${INPUT}`}
            >
              {TAX_ID_TYPES.map((t) => (
                <option key={t.value} value={t.value}>{t.label}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-xs font-medium uppercase tracking-wide text-text-muted">Country</label>
            <input name="tax_id_country" defaultValue={cfg.country || "US"} key={cfg.value} className={`mt-1 w-full ${INPUT}`} />
          </div>
          <div className="sm:col-span-2">
            <label className="block text-xs font-medium uppercase tracking-wide text-text-muted">
              Value {cfg.format && <span className="ml-1 font-mono text-text-secondary">format: {cfg.format}</span>}
            </label>
            <input name="value" placeholder={cfg.format || "Tax identifier"} required className={`mt-1 w-full ${INPUT}`} />
            <p className="mt-1 text-xs text-text-muted">
              Stored encrypted — only the masked form ({cfg.mask}) is shown after saving.
            </p>
          </div>
          <label className="flex items-center gap-2 text-sm text-text-secondary">
            <input type="checkbox" name="is_primary" defaultChecked /> Primary
          </label>
          {state?.error && <p className="text-sm text-[#9B2335] sm:col-span-2">{state.error}</p>}
          <div className="flex gap-2 sm:col-span-2">
            <button type="submit" disabled={pending} className="rounded-md bg-navy px-4 py-2 text-sm font-medium text-bg-app hover:opacity-90 disabled:opacity-60">
              {pending ? "Saving…" : "Save tax ID"}
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
