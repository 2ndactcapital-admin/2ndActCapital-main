"use client";

import { useActionState, useState } from "react";
import { createEntityAction } from "@/lib/actions";
import {
  ENTITY_TYPES,
  STATUS_OPTIONS,
  subTypesFor,
  FREE_TEXT_SUBTYPE_TYPES,
} from "@/lib/entityTypes";

const INPUT_CLASS =
  "mt-1 w-full rounded-md border border-border bg-bg-card px-3 py-2 text-sm text-text-primary outline-none focus:ring-2 focus:ring-navy";
const LABEL_CLASS =
  "block text-xs font-medium uppercase tracking-wide text-text-muted";

export default function NewEntityForm() {
  const [state, formAction, pending] = useActionState(createEntityAction, {});
  const [entityType, setEntityType] = useState("individual");

  const subTypes = subTypesFor(entityType);
  const freeTextSubType = FREE_TEXT_SUBTYPE_TYPES.includes(entityType);

  return (
    <form action={formAction} className="max-w-2xl space-y-4">
      <div className="grid gap-4 sm:grid-cols-2">
        <div>
          <label className={LABEL_CLASS}>Entity Type</label>
          <select
            name="entity_type"
            value={entityType}
            onChange={(e) => setEntityType(e.target.value)}
            className={INPUT_CLASS}
          >
            {ENTITY_TYPES.map((t) => (
              <option key={t.value} value={t.value}>
                {t.label}
              </option>
            ))}
          </select>
        </div>

        {/* Sub-type — dynamic on entity type; hidden when none apply */}
        {subTypes.length > 0 ? (
          <div>
            <label className={LABEL_CLASS}>Sub-type</label>
            <select name="sub_type" defaultValue="" className={INPUT_CLASS}>
              <option value="">Select…</option>
              {subTypes.map((st) => (
                <option key={st} value={st}>
                  {st}
                </option>
              ))}
            </select>
          </div>
        ) : freeTextSubType ? (
          <div>
            <label className={LABEL_CLASS}>Sub-type</label>
            <input name="sub_type" className={INPUT_CLASS} />
          </div>
        ) : (
          <div />
        )}
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
          <label className={LABEL_CLASS}>Status</label>
          <select name="status" defaultValue="prospect" className={INPUT_CLASS}>
            {STATUS_OPTIONS.map((s) => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className={LABEL_CLASS}>Lead Source</label>
          <input name="lead_source" className={INPUT_CLASS} />
        </div>
        <div>
          <label className={LABEL_CLASS}>Country of Formation</label>
          <input name="country_of_formation" className={INPUT_CLASS} />
        </div>
        <div>
          <label className={LABEL_CLASS}>Date of Birth</label>
          <input type="date" name="date_of_birth" className={INPUT_CLASS} />
        </div>
        <div>
          <label className={LABEL_CLASS}>Primary Email</label>
          <input type="email" name="primary_email" className={INPUT_CLASS} />
        </div>
        <div>
          <label className={LABEL_CLASS}>Primary Phone</label>
          <input name="primary_phone" className={INPUT_CLASS} />
        </div>
        <div>
          <label className={LABEL_CLASS}>Tags (comma-separated)</label>
          <input name="tags" placeholder="vip, referral" className={INPUT_CLASS} />
        </div>
      </div>

      <div>
        <label className={LABEL_CLASS}>LinkedIn — import coming soon</label>
        <input
          name="linkedin_url"
          placeholder="https://linkedin.com/in/…"
          className={INPUT_CLASS}
        />
      </div>

      <div>
        <label className={LABEL_CLASS}>Notes</label>
        <textarea name="notes" rows={4} className={INPUT_CLASS} />
      </div>

      {state?.error && <p className="text-sm text-[#9B2335]">{state.error}</p>}

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
