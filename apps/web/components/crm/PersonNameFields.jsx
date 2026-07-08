"use client";

import { useState } from "react";
import { ReferenceSelect } from "@/components/ReferenceSelect";

const INPUT_CLASS =
  "mt-1 w-full rounded-md border border-border bg-bg-card px-3 py-2 text-sm text-text-primary outline-none focus:ring-2 focus:ring-navy";
const LABEL_CLASS =
  "block text-xs font-medium uppercase tracking-wide text-text-muted";

export default function PersonNameFields({ defaultValues = {} }) {
  const [legalNameOverridden, setLegalNameOverridden] = useState(
    defaultValues.legal_name_overridden || false,
  );

  return (
    <>
      <div>
        <label className={LABEL_CLASS}>Prefix</label>
        <ReferenceSelect
          listKey="name_prefix"
          name="name_prefix"
          defaultValue={defaultValues.name_prefix || ""}
          className={INPUT_CLASS}
          placeholder="Select…"
        />
      </div>
      <div>
        <label className={LABEL_CLASS}>First Name</label>
        <input
          name="first_name"
          defaultValue={defaultValues.first_name || ""}
          className={INPUT_CLASS}
        />
      </div>
      <div>
        <label className={LABEL_CLASS}>Middle Name</label>
        <input
          name="middle_name"
          defaultValue={defaultValues.middle_name || ""}
          className={INPUT_CLASS}
        />
      </div>
      <div>
        <label className={LABEL_CLASS}>Last Name / Surname</label>
        <input
          name="surname"
          defaultValue={defaultValues.surname || ""}
          className={INPUT_CLASS}
        />
      </div>
      <div>
        <label className={LABEL_CLASS}>Suffix</label>
        <ReferenceSelect
          listKey="name_suffix"
          name="name_suffix"
          defaultValue={defaultValues.name_suffix || ""}
          className={INPUT_CLASS}
          placeholder="Select…"
        />
      </div>
      <div className="flex items-center gap-2 pt-5">
        <input
          type="checkbox"
          name="legal_name_overridden"
          id="lno"
          checked={legalNameOverridden}
          onChange={(e) => setLegalNameOverridden(e.target.checked)}
        />
        <label htmlFor="lno" className="text-xs text-text-secondary">
          Override legal name manually
        </label>
      </div>
      {legalNameOverridden && (
        <div>
          <label className={LABEL_CLASS}>Legal Name (manual)</label>
          <input
            name="legal_name"
            defaultValue={defaultValues.legal_name || ""}
            className={INPUT_CLASS}
          />
        </div>
      )}
    </>
  );
}
