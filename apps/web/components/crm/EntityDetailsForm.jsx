"use client";

import { useActionState, useEffect, useState } from "react";
import { updateEntityAction } from "@/lib/actions";

const INPUT_CLASS =
  "w-full rounded-md border border-border bg-bg-card px-3 py-2 text-sm text-text-primary outline-none focus:ring-2 focus:ring-navy";

const FIELDS = [
  { key: "display_name", label: "Display Name" },
  { key: "legal_name", label: "Legal Name" },
  { key: "tax_id", label: "Tax ID" },
  { key: "country_of_formation", label: "Country" },
];

function ReadRow({ label, value }) {
  return (
    <div>
      <dt className="text-xs font-medium uppercase tracking-wide text-text-muted">
        {label}
      </dt>
      <dd className="mt-0.5 text-sm text-text-primary">{value || "—"}</dd>
    </div>
  );
}

export default function EntityDetailsForm({ entity }) {
  const [editing, setEditing] = useState(false);
  const [state, formAction, pending] = useActionState(
    updateEntityAction.bind(null, entity.id),
    {},
  );

  useEffect(() => {
    if (state?.ok) setEditing(false);
  }, [state]);

  if (!editing) {
    return (
      <div>
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold text-text-secondary">Details</h2>
          <button
            type="button"
            onClick={() => setEditing(true)}
            className="text-sm font-medium text-navy hover:underline"
          >
            Edit
          </button>
        </div>
        <dl className="mt-4 space-y-4">
          {FIELDS.map((f) => (
            <ReadRow key={f.key} label={f.label} value={entity[f.key]} />
          ))}
          <ReadRow label="Notes" value={entity.notes} />
        </dl>
      </div>
    );
  }

  return (
    <form action={formAction}>
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-text-secondary">Edit Details</h2>
      </div>
      <div className="mt-4 space-y-3">
        {FIELDS.map((f) => (
          <div key={f.key}>
            <label className="block text-xs font-medium uppercase tracking-wide text-text-muted">
              {f.label}
            </label>
            <input
              name={f.key}
              defaultValue={entity[f.key] || ""}
              className={`mt-1 ${INPUT_CLASS}`}
            />
          </div>
        ))}
        <div>
          <label className="block text-xs font-medium uppercase tracking-wide text-text-muted">
            Notes
          </label>
          <textarea
            name="notes"
            rows={3}
            defaultValue={entity.notes || ""}
            className={`mt-1 ${INPUT_CLASS}`}
          />
        </div>
        {state?.error && <p className="text-sm text-red-600">{state.error}</p>}
        <div className="flex gap-2">
          <button
            type="submit"
            disabled={pending}
            className="rounded-md bg-navy px-4 py-2 text-sm font-medium text-bg-app transition-opacity hover:opacity-90 disabled:opacity-60"
          >
            {pending ? "Saving…" : "Save"}
          </button>
          <button
            type="button"
            onClick={() => setEditing(false)}
            className="rounded-md border border-border px-4 py-2 text-sm font-medium text-text-secondary hover:bg-border"
          >
            Cancel
          </button>
        </div>
      </div>
    </form>
  );
}
