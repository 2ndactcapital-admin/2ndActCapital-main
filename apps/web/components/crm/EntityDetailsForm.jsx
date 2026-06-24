"use client";

import { useActionState, useEffect, useState } from "react";
import { updateEntityAction } from "@/lib/actions";
import { STATUS_OPTIONS, statusLabel, subTypesFor, FREE_TEXT_SUBTYPE_TYPES } from "@/lib/entityTypes";

const INPUT_CLASS =
  "mt-1 w-full rounded-md border border-border bg-bg-card px-3 py-2 text-sm text-text-primary outline-none focus:ring-2 focus:ring-navy";
const LABEL_CLASS =
  "block text-xs font-medium uppercase tracking-wide text-text-muted";

const TEXT_FIELDS = [
  { key: "display_name", label: "Display Name" },
  { key: "legal_name", label: "Legal Name" },
  { key: "lead_source", label: "Lead Source" },
  { key: "country_of_formation", label: "Country" },
  { key: "primary_email", label: "Primary Email" },
  { key: "primary_phone", label: "Primary Phone" },
];

function StatusBadge({ status }) {
  if (!status) return <span className="text-sm text-text-muted">—</span>;
  return (
    <span className="inline-flex items-center rounded-full bg-gold-light px-2.5 py-0.5 text-xs font-medium text-navy">
      {statusLabel(status)}
    </span>
  );
}

function ReadRow({ label, children }) {
  return (
    <div>
      <dt className="text-xs font-medium uppercase tracking-wide text-text-muted">
        {label}
      </dt>
      <dd className="mt-0.5 text-sm text-text-primary">{children}</dd>
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

  const subTypes = subTypesFor(entity.entity_type);
  const freeTextSubType = FREE_TEXT_SUBTYPE_TYPES.includes(entity.entity_type);
  const tags = entity.tags || [];

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
        <dl className="mt-4 grid gap-4 sm:grid-cols-2">
          <ReadRow label="Status">
            <StatusBadge status={entity.status} />
          </ReadRow>
          {entity.sub_type && <ReadRow label="Sub-type">{entity.sub_type}</ReadRow>}
          {TEXT_FIELDS.map((f) => (
            <ReadRow key={f.key} label={f.label}>
              {entity[f.key] || "—"}
            </ReadRow>
          ))}
          <ReadRow label="Tags">
            {tags.length ? (
              <span className="flex flex-wrap gap-1">
                {tags.map((t) => (
                  <span
                    key={t}
                    className="rounded-full border border-border px-2 py-0.5 text-xs text-text-secondary"
                  >
                    {t}
                  </span>
                ))}
              </span>
            ) : (
              "—"
            )}
          </ReadRow>
          <div className="sm:col-span-2">
            <ReadRow label="Notes">{entity.notes || "—"}</ReadRow>
          </div>
        </dl>
      </div>
    );
  }

  return (
    <form action={formAction}>
      <h2 className="text-sm font-semibold text-text-secondary">Edit Details</h2>
      <div className="mt-4 grid gap-3 sm:grid-cols-2">
        {TEXT_FIELDS.map((f) => (
          <div key={f.key}>
            <label className={LABEL_CLASS}>{f.label}</label>
            <input name={f.key} defaultValue={entity[f.key] || ""} className={INPUT_CLASS} />
          </div>
        ))}
        <div>
          <label className={LABEL_CLASS}>Status</label>
          <select name="status" defaultValue={entity.status || "prospect"} className={INPUT_CLASS}>
            {STATUS_OPTIONS.map((s) => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
          </select>
        </div>
        {subTypes.length > 0 ? (
          <div>
            <label className={LABEL_CLASS}>Sub-type</label>
            <select name="sub_type" defaultValue={entity.sub_type || ""} className={INPUT_CLASS}>
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
            <input name="sub_type" defaultValue={entity.sub_type || ""} className={INPUT_CLASS} />
          </div>
        ) : null}
        <div className="sm:col-span-2">
          <label className={LABEL_CLASS}>Tags (comma-separated)</label>
          <input name="tags" defaultValue={(entity.tags || []).join(", ")} className={INPUT_CLASS} />
        </div>
        <div className="sm:col-span-2">
          <label className={LABEL_CLASS}>Notes</label>
          <textarea name="notes" rows={3} defaultValue={entity.notes || ""} className={INPUT_CLASS} />
        </div>
      </div>
      {state?.error && <p className="mt-2 text-sm text-[#9B2335]">{state.error}</p>}
      <div className="mt-3 flex gap-2">
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
    </form>
  );
}
