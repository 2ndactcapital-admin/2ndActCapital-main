"use client";

import { useActionState } from "react";
import { createEntityAction } from "@/lib/actions";
import { ENTITY_TYPES } from "@/lib/entityTypes";

const INPUT_CLASS =
  "mt-1 w-full rounded-md border border-border bg-bg-card px-3 py-2 text-sm text-text-primary outline-none focus:ring-2 focus:ring-navy";
const LABEL_CLASS =
  "block text-xs font-medium uppercase tracking-wide text-text-muted";

export default function NewEntityForm() {
  const [state, formAction, pending] = useActionState(createEntityAction, {});

  return (
    <form action={formAction} className="max-w-2xl space-y-4">
      <div>
        <label className={LABEL_CLASS}>Entity Type</label>
        <select name="entity_type" defaultValue="individual" className={INPUT_CLASS}>
          {ENTITY_TYPES.map((t) => (
            <option key={t.value} value={t.value}>
              {t.label}
            </option>
          ))}
        </select>
      </div>

      <div>
        <label className={LABEL_CLASS}>Display Name *</label>
        <input name="display_name" required className={INPUT_CLASS} />
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <div>
          <label className={LABEL_CLASS}>Legal Name</label>
          <input name="legal_name" className={INPUT_CLASS} />
        </div>
        <div>
          <label className={LABEL_CLASS}>Tax ID</label>
          <input name="tax_id" className={INPUT_CLASS} />
        </div>
        <div>
          <label className={LABEL_CLASS}>Date of Birth</label>
          <input type="date" name="date_of_birth" className={INPUT_CLASS} />
        </div>
        <div>
          <label className={LABEL_CLASS}>Country of Formation</label>
          <input name="country_of_formation" className={INPUT_CLASS} />
        </div>
      </div>

      <div>
        <label className={LABEL_CLASS}>Notes</label>
        <textarea name="notes" rows={4} className={INPUT_CLASS} />
      </div>

      {state?.error && <p className="text-sm text-red-600">{state.error}</p>}

      <div className="flex gap-3">
        <button
          type="submit"
          disabled={pending}
          className="rounded-md bg-navy px-5 py-2 text-sm font-medium text-bg-app transition-opacity hover:opacity-90 disabled:opacity-60"
        >
          {pending ? "Creating…" : "Create Entity"}
        </button>
        <a
          href="/crm"
          className="rounded-md border border-border px-5 py-2 text-sm font-medium text-text-secondary hover:bg-border"
        >
          Cancel
        </a>
      </div>
    </form>
  );
}
