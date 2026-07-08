"use client";

import { useActionState, useEffect, useState } from "react";
import { updateEntityAction } from "@/lib/actions";
import { STATUS_OPTIONS, statusLabel, subTypesFor, FREE_TEXT_SUBTYPE_TYPES } from "@/lib/entityTypes";
import { CountryRegionSelect, ReferenceSelect } from "@/components/ReferenceSelect";

const INPUT_CLASS =
  "mt-1 w-full rounded-md border border-border bg-bg-card px-3 py-2 text-sm text-text-primary outline-none focus:ring-2 focus:ring-navy";
const LABEL_CLASS =
  "block text-xs font-medium uppercase tracking-wide text-text-muted";

const PERSON_TYPES = new Set(["individual"]);

// Text fields shown for all entity types
const COMMON_TEXT_FIELDS = [
  { key: "lead_source", label: "Lead Source" },
  { key: "primary_email", label: "Primary Email" },
  { key: "primary_phone", label: "Primary Phone" },
  { key: "url", label: "Website / URL" },
];

// Text fields only for non-person entities
const ENTITY_TEXT_FIELDS = [
  { key: "display_name", label: "Display Name" },
  { key: "legal_name", label: "Legal Name" },
  { key: "country_of_formation", label: "Country of Formation" },
];

// Text fields only for persons
const PERSON_TEXT_FIELDS = [
  { key: "display_name", label: "Display Name" },
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
  const [confirmInactive, setConfirmInactive] = useState(false);
  const [legalNameOverridden, setLegalNameOverridden] = useState(entity.legal_name_overridden || false);
  const [state, formAction, pending] = useActionState(
    updateEntityAction.bind(null, entity.id),
    {},
  );

  useEffect(() => {
    if (state?.ok) setEditing(false);
  }, [state]);

  const isPerson = PERSON_TYPES.has(entity.entity_type);
  const subTypes = subTypesFor(entity.entity_type);
  const freeTextSubType = FREE_TEXT_SUBTYPE_TYPES.includes(entity.entity_type);
  const tags = entity.tags || [];
  const dateLabel = isPerson ? "Date of Birth" : "Date of Formation";
  const endDateLabel = isPerson ? "Date of Death" : "End / Dissolution Date";
  const countryRegionLabel = isPerson ? "Country (Citizenship) / Region" : "Country / Region";

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
          {!entity.is_active && (
            <ReadRow label="Active">
              <span className="text-xs font-semibold uppercase text-[#9B2335]">Inactive</span>
            </ReadRow>
          )}
          {entity.sub_type && <ReadRow label="Sub-type">{entity.sub_type}</ReadRow>}
          <ReadRow label="Display Name">{entity.display_name || "—"}</ReadRow>
          {isPerson ? (
            <>
              <ReadRow label="Legal Name">{entity.legal_name || "—"}</ReadRow>
              <ReadRow label="First Name">{entity.first_name || "—"}</ReadRow>
              <ReadRow label="Last Name">{entity.surname || "—"}</ReadRow>
            </>
          ) : (
            <>
              <ReadRow label="Legal Name">{entity.legal_name || "—"}</ReadRow>
              <ReadRow label="Country of Formation">{entity.country_of_formation || "—"}</ReadRow>
            </>
          )}
          {entity.inception_date && (
            <ReadRow label={dateLabel}>{String(entity.inception_date)}</ReadRow>
          )}
          {entity.end_date && (
            <ReadRow label={endDateLabel}>{String(entity.end_date)}</ReadRow>
          )}
          {(entity.country_code || entity.region_code) && (
            <ReadRow label={countryRegionLabel}>
              {[entity.country_code, entity.region_code].filter(Boolean).join(" / ")}
            </ReadRow>
          )}
          {entity.url && <ReadRow label="Website">{entity.url}</ReadRow>}
          <ReadRow label="Lead Source">{entity.lead_source || "—"}</ReadRow>
          <ReadRow label="Primary Email">{entity.primary_email || "—"}</ReadRow>
          <ReadRow label="Primary Phone">{entity.primary_phone || "—"}</ReadRow>
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

        {/* Display name */}
        <div>
          <label className={LABEL_CLASS}>Display Name</label>
          <input name="display_name" defaultValue={entity.display_name || ""} className={INPUT_CLASS} />
        </div>

        {/* Person name components */}
        {isPerson && (
          <>
            <div>
              <label className={LABEL_CLASS}>Prefix</label>
              <ReferenceSelect listKey="name_prefix" name="name_prefix" defaultValue={entity.name_prefix || ""} className={INPUT_CLASS} placeholder="Select…" />
            </div>
            <div>
              <label className={LABEL_CLASS}>First Name</label>
              <input name="first_name" defaultValue={entity.first_name || ""} className={INPUT_CLASS} />
            </div>
            <div>
              <label className={LABEL_CLASS}>Middle Name</label>
              <input name="middle_name" defaultValue={entity.middle_name || ""} className={INPUT_CLASS} />
            </div>
            <div>
              <label className={LABEL_CLASS}>Last Name / Surname</label>
              <input name="surname" defaultValue={entity.surname || ""} className={INPUT_CLASS} />
            </div>
            <div>
              <label className={LABEL_CLASS}>Suffix</label>
              <ReferenceSelect listKey="name_suffix" name="name_suffix" defaultValue={entity.name_suffix || ""} className={INPUT_CLASS} placeholder="Select…" />
            </div>
            <div className="flex items-center gap-2 pt-5">
              <input type="checkbox" name="legal_name_overridden" id="lno" checked={legalNameOverridden} onChange={(e) => setLegalNameOverridden(e.target.checked)} />
              <label htmlFor="lno" className="text-xs text-text-secondary">Override legal name manually</label>
            </div>
            {legalNameOverridden && (
              <div>
                <label className={LABEL_CLASS}>Legal Name (manual)</label>
                <input name="legal_name" defaultValue={entity.legal_name || ""} className={INPUT_CLASS} />
              </div>
            )}
          </>
        )}

        {/* Non-person fields */}
        {!isPerson && (
          <>
            <div>
              <label className={LABEL_CLASS}>Legal Name</label>
              <input name="legal_name" defaultValue={entity.legal_name || ""} className={INPUT_CLASS} />
            </div>
            <div>
              <label className={LABEL_CLASS}>Country of Formation</label>
              <ReferenceSelect listKey="country" name="country_of_formation" defaultValue={entity.country_of_formation || ""} className={INPUT_CLASS} placeholder="Select country…" />
            </div>
          </>
        )}

        {/* Dates */}
        <div>
          <label className={LABEL_CLASS}>{dateLabel}</label>
          <input type="date" name="inception_date" defaultValue={entity.inception_date || ""} className={INPUT_CLASS} />
        </div>
        <div>
          <label className={LABEL_CLASS}>{endDateLabel}</label>
          <input type="date" name="end_date" defaultValue={entity.end_date || ""} className={INPUT_CLASS} />
        </div>

        {/* Country / Region */}
        <div className="sm:col-span-2">
          <label className={LABEL_CLASS}>{countryRegionLabel}</label>
          <CountryRegionSelect
            defaultCountryCode={entity.country_code || ""}
            defaultRegionCode={entity.region_code || ""}
          />
        </div>

        {/* URL */}
        <div>
          <label className={LABEL_CLASS}>Website / URL</label>
          <input name="url" defaultValue={entity.url || ""} className={INPUT_CLASS} placeholder="https://…" />
        </div>

        {/* Common fields */}
        <div>
          <label className={LABEL_CLASS}>Lead Source</label>
          <input name="lead_source" defaultValue={entity.lead_source || ""} className={INPUT_CLASS} />
        </div>
        <div>
          <label className={LABEL_CLASS}>Primary Email</label>
          <input name="primary_email" defaultValue={entity.primary_email || ""} className={INPUT_CLASS} />
        </div>
        <div>
          <label className={LABEL_CLASS}>Primary Phone</label>
          <input name="primary_phone" defaultValue={entity.primary_phone || ""} className={INPUT_CLASS} />
        </div>

        {/* Status */}
        <div>
          <label className={LABEL_CLASS}>Status</label>
          <select name="status" defaultValue={entity.status || "prospect"} className={INPUT_CLASS}>
            {STATUS_OPTIONS.map((s) => (
              <option key={s.value} value={s.value}>{s.label}</option>
            ))}
          </select>
        </div>

        {/* Sub-type */}
        {subTypes.length > 0 ? (
          <div>
            <label className={LABEL_CLASS}>Sub-type</label>
            <select name="sub_type" defaultValue={entity.sub_type || ""} className={INPUT_CLASS}>
              <option value="">Select…</option>
              {subTypes.map((st) => (
                <option key={st} value={st}>{st}</option>
              ))}
            </select>
          </div>
        ) : freeTextSubType ? (
          <div>
            <label className={LABEL_CLASS}>Sub-type</label>
            <input name="sub_type" defaultValue={entity.sub_type || ""} className={INPUT_CLASS} />
          </div>
        ) : null}

        {/* is_active — hidden sentinel so unchecked = false (not missing) */}
        <div className="sm:col-span-2">
          <input type="hidden" name="is_active_sentinel" value="1" />
          <label className="flex items-center gap-2 text-sm text-text-secondary">
            <input
              type="checkbox"
              name="is_active"
              defaultChecked={entity.is_active !== false}
              onChange={(e) => {
                if (!e.target.checked) setConfirmInactive(true);
                else setConfirmInactive(false);
              }}
            />
            Active (uncheck to mark this entity inactive)
          </label>
          {confirmInactive && (
            <p className="mt-1 text-xs text-[#9B2335]">
              Inactive entities are hidden from default lists. Save to confirm.
            </p>
          )}
        </div>

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
          onClick={() => { setEditing(false); setConfirmInactive(false); }}
          className="rounded-md border border-border px-4 py-2 text-sm font-medium text-text-secondary hover:bg-border"
        >
          Cancel
        </button>
      </div>
    </form>
  );
}
