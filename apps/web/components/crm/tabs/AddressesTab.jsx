"use client";

import { useActionState, useEffect, useRef, useState } from "react";
import { addAddressAction } from "@/lib/crmActions";

const INPUT = "rounded-md border border-border bg-bg-card px-3 py-2 text-sm text-text-primary outline-none focus:ring-2 focus:ring-navy";
const ADDRESS_TYPES = [
  { value: "primary_residence", label: "Primary Residence" },
  { value: "mailing", label: "Mailing" },
  { value: "business", label: "Business" },
  { value: "registered", label: "Registered" },
];

function typeLabel(v) {
  return ADDRESS_TYPES.find((t) => t.value === v)?.label || v;
}

export default function AddressesTab({ entityId, initial }) {
  const [items, setItems] = useState(initial || []);
  const [adding, setAdding] = useState(false);
  const formRef = useRef(null);
  const [state, formAction, pending] = useActionState(
    addAddressAction.bind(null, entityId),
    {},
  );

  useEffect(() => {
    if (state?.ok && state.item) {
      setItems((prev) =>
        prev.some((a) => a.id === state.item.id) ? prev : [...prev, state.item],
      );
      formRef.current?.reset();
      setAdding(false);
    }
  }, [state]);

  return (
    <div>
      <div className="flex items-center justify-between">
        <h2 className="text-base font-semibold text-navy">Addresses</h2>
        {!adding && (
          <button type="button" onClick={() => setAdding(true)} className="text-sm font-medium text-navy hover:underline">
            Add address
          </button>
        )}
      </div>

      {items.length === 0 ? (
        <p className="mt-3 text-sm text-text-muted">No addresses on file.</p>
      ) : (
        <ul className="mt-3 space-y-2">
          {items.map((a) => (
            <li key={a.id} className="rounded-lg border border-border bg-bg-card p-4">
              <div className="flex items-center gap-2">
                <span className="inline-flex items-center rounded-full bg-gold-light px-2.5 py-0.5 text-xs font-medium text-navy">
                  {typeLabel(a.address_type)}
                </span>
                {a.is_primary && <span className="text-xs text-text-muted">Primary</span>}
              </div>
              <p className="mt-2 text-sm text-text-primary">
                {[a.street1, a.street2, a.city, a.state, a.postal_code, a.country].filter(Boolean).join(", ")}
              </p>
            </li>
          ))}
        </ul>
      )}

      {adding && (
        <form ref={formRef} action={formAction} className="mt-4 grid max-w-xl gap-3 sm:grid-cols-2">
          <select name="address_type" defaultValue="primary_residence" className={INPUT}>
            {ADDRESS_TYPES.map((t) => (
              <option key={t.value} value={t.value}>{t.label}</option>
            ))}
          </select>
          <div />
          <input name="street1" placeholder="Street 1 *" required className={INPUT} />
          <input name="street2" placeholder="Street 2" className={INPUT} />
          <input name="city" placeholder="City *" required className={INPUT} />
          <input name="state" placeholder="State / Region" className={INPUT} />
          <input name="postal_code" placeholder="Postal code" className={INPUT} />
          <input name="country" placeholder="Country" defaultValue="US" className={INPUT} />
          <label className="flex items-center gap-2 text-sm text-text-secondary">
            <input type="checkbox" name="is_primary" /> Primary address
          </label>
          {state?.error && <p className="text-sm text-[#9B2335] sm:col-span-2">{state.error}</p>}
          <div className="flex gap-2 sm:col-span-2">
            <button type="submit" disabled={pending} className="rounded-md bg-navy px-4 py-2 text-sm font-medium text-bg-app hover:opacity-90 disabled:opacity-60">
              {pending ? "Saving…" : "Save address"}
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
