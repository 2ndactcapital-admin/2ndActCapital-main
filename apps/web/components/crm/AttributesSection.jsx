"use client";

import { useActionState, useEffect, useRef, useState } from "react";
import { addAttributeAction } from "@/lib/actions";

const INPUT_CLASS =
  "rounded-md border border-border bg-bg-card px-3 py-2 text-sm text-text-primary outline-none focus:ring-2 focus:ring-navy";

export default function AttributesSection({ entityId, attributes }) {
  const formRef = useRef(null);
  const [adding, setAdding] = useState(false);
  const [state, formAction, pending] = useActionState(
    addAttributeAction.bind(null, entityId),
    {},
  );

  useEffect(() => {
    if (state?.ok) {
      formRef.current?.reset();
      setAdding(false);
    }
  }, [state]);

  return (
    <section>
      <div className="flex items-center justify-between">
        <h2 className="text-base font-semibold text-navy">Attributes</h2>
        {!adding && (
          <button
            type="button"
            onClick={() => setAdding(true)}
            className="text-sm font-medium text-navy hover:underline"
          >
            Add attribute
          </button>
        )}
      </div>

      {attributes.length === 0 ? (
        <p className="mt-3 text-sm text-text-muted">No attributes yet.</p>
      ) : (
        <dl className="mt-3 divide-y divide-border rounded-lg border border-border bg-bg-card">
          {attributes.map((attr) => (
            <div key={attr.id} className="flex justify-between px-4 py-2.5">
              <dt className="text-sm text-text-secondary">{attr.attribute_key}</dt>
              <dd className="text-sm font-medium text-text-primary">
                {attr.attribute_value || "—"}
              </dd>
            </div>
          ))}
        </dl>
      )}

      {adding && (
        <form ref={formRef} action={formAction} className="mt-3 flex flex-wrap items-end gap-2">
          <div>
            <label className="block text-xs font-medium uppercase tracking-wide text-text-muted">
              Key
            </label>
            <input name="attribute_key" required className={`mt-1 ${INPUT_CLASS}`} />
          </div>
          <div>
            <label className="block text-xs font-medium uppercase tracking-wide text-text-muted">
              Value
            </label>
            <input name="attribute_value" className={`mt-1 ${INPUT_CLASS}`} />
          </div>
          <input type="hidden" name="value_type" value="string" />
          <button
            type="submit"
            disabled={pending}
            className="rounded-md bg-navy px-4 py-2 text-sm font-medium text-bg-app transition-opacity hover:opacity-90 disabled:opacity-60"
          >
            {pending ? "Adding…" : "Add"}
          </button>
          <button
            type="button"
            onClick={() => setAdding(false)}
            className="rounded-md border border-border px-4 py-2 text-sm font-medium text-text-secondary hover:bg-border"
          >
            Cancel
          </button>
          {state?.error && (
            <p className="w-full text-sm text-red-600">{state.error}</p>
          )}
        </form>
      )}
    </section>
  );
}
