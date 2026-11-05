"use client";

import { useActionState, useState } from "react";
import { createEntityAction } from "@/lib/actions";
import { ENTITY_TYPES, PERSON_TYPES } from "@/lib/entityTypes";
import { freeTextSubType as isFreeTextSubType, maritalOptions, statusOptions, subTypeOptions } from "@/lib/crmVocab.mjs";
import { useCrmVocab } from "@/components/crm/useCrmVocab";
import { ReferenceSelect } from "@/components/ReferenceSelect";
import PersonNameFields from "@/components/crm/PersonNameFields";
import DateInput from "@/components/ui/DateInput";

const INPUT_CLASS =
  "mt-1 w-full rounded-md border border-border bg-bg-card px-3 py-2 text-sm text-text-primary outline-none focus:ring-2 focus:ring-navy";
const LABEL_CLASS =
  "block text-xs font-medium uppercase tracking-wide text-text-muted";

export default function NewEntityForm() {
  const [state, formAction, pending] = useActionState(createEntityAction, {});
  const [entityType, setEntityType] = useState("individual");

  // PORTENTRY1 — vocabularies from the API envelope (the org's config table), never a page list
  const vocab = useCrmVocab();
  const isPerson = PERSON_TYPES.has(entityType);
  const subTypes = subTypeOptions(vocab, entityType, null);
  const freeTextSubType = isFreeTextSubType(vocab, entityType);
  const dateLabel = isPerson ? "Date of Birth" : "Date of Formation";

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
                <option key={st.value} value={st.value}>
                  {st.label}
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

      {/* Person name components or single legal name, depending on entity type */}
      <div className="grid gap-4 sm:grid-cols-2">
        {isPerson ? (
          <PersonNameFields />
        ) : (
          <div>
            <label className={LABEL_CLASS}>Legal Name</label>
            <input name="legal_name" className={INPUT_CLASS} />
          </div>
        )}
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <div>
          <label className={LABEL_CLASS}>Status</label>
          <select name="status" defaultValue="prospect" className={INPUT_CLASS}>
            {statusOptions(vocab, "prospect").map((s) => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
          </select>
        </div>
        {isPerson && (
          <div>
            <label className={LABEL_CLASS}>Marital Status</label>
            <select name="marital_status" defaultValue="" className={INPUT_CLASS}>
              <option value="">Not recorded</option>
              {maritalOptions(vocab).map((m) => (
                <option key={m.value} value={m.value}>{m.label}</option>
              ))}
            </select>
          </div>
        )}
        <div>
          <label className={LABEL_CLASS}>Lead Source</label>
          <input name="lead_source" className={INPUT_CLASS} />
        </div>

        {!isPerson && (
          <div>
            <label className={LABEL_CLASS}>Country of Formation</label>
            <ReferenceSelect
              listKey="country"
              name="country_of_formation"
              className={INPUT_CLASS}
              placeholder="Select country…"
            />
          </div>
        )}

        <div>
          <label className={LABEL_CLASS}>{dateLabel}</label>
          <DateInput name="inception_date" className={INPUT_CLASS} />
        </div>
        {/* PORTENTRY2 (MUSE-OLIVO-B112-01) — maiden name and date of death on New, not only on Edit */}
        {isPerson && (
          <>
            <div>
              <label className={LABEL_CLASS}>Maiden Name</label>
              <input name="maiden_name" className={INPUT_CLASS} />
            </div>
            <div>
              <label className={LABEL_CLASS}>Date of Death</label>
              <DateInput name="date_of_death" className={INPUT_CLASS} />
            </div>
          </>
        )}
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
