"use client";

import { useActionState, useRef, useState } from "react";
import { IconPlus, IconX } from "@tabler/icons-react";
import { createDealAction } from "@/lib/marketplaceActions";
import { searchEntitiesAction } from "@/lib/crmActions";

const INPUT =
  "mt-1 w-full rounded-md border border-border bg-bg-card px-3 py-2 text-sm text-text-primary outline-none focus:ring-2 focus:ring-navy";
const LABEL = "block text-xs font-medium uppercase tracking-wide text-text-muted";

function SponsorTypeahead() {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState([]);
  const [selected, setSelected] = useState(null);
  const timer = useRef(null);

  function onChange(value) {
    setQuery(value);
    setSelected(null);
    clearTimeout(timer.current);
    if (!value.trim()) {
      setResults([]);
      return;
    }
    timer.current = setTimeout(async () => {
      const res = await searchEntitiesAction(value);
      setResults(res.results || []);
    }, 300);
  }

  return (
    <div>
      <label className={LABEL}>Sponsor (search existing entities)</label>
      <input type="hidden" name="sponsor_entity_id" value={selected?.id || ""} />
      <input
        value={selected ? selected.display_name : query}
        onChange={(e) => onChange(e.target.value)}
        placeholder="Type to search…"
        className={INPUT}
      />
      {!selected && results.length > 0 && (
        <ul className="mt-1 max-h-40 overflow-auto rounded-md border border-border bg-bg-card">
          {results.map((e) => (
            <li key={e.id}>
              <button
                type="button"
                onClick={() => {
                  setSelected(e);
                  setResults([]);
                }}
                className="block w-full px-3 py-2 text-left text-sm text-text-primary hover:bg-bg-app"
              >
                {e.display_name}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function ClassField({ name, label, options }) {
  if (options && options.length > 0) {
    return (
      <div>
        <label className={LABEL}>{label}</label>
        <select name={name} className={INPUT} defaultValue="">
          <option value="">Select…</option>
          {options.map((o) => (
            <option key={o} value={o}>
              {o}
            </option>
          ))}
        </select>
      </div>
    );
  }
  return (
    <div>
      <label className={LABEL}>{label}</label>
      <input name={name} className={INPUT} />
    </div>
  );
}

export default function NewDealForm({ superClasses = [], assetClasses = [] }) {
  const [state, formAction, pending] = useActionState(createDealAction, {});
  const [highlights, setHighlights] = useState([""]);

  function setHighlight(i, value) {
    setHighlights((prev) => prev.map((h, idx) => (idx === i ? value : h)));
  }
  function addHighlight() {
    setHighlights((prev) => [...prev, ""]);
  }
  function removeHighlight(i) {
    setHighlights((prev) => prev.filter((_, idx) => idx !== i));
  }

  return (
    <form action={formAction} className="max-w-2xl space-y-5">
      <input
        type="hidden"
        name="highlights"
        value={highlights.filter(Boolean).join("\n")}
      />

      <div>
        <label className={LABEL}>Deal name *</label>
        <input name="name" required className={INPUT} />
      </div>

      <div>
        <label className={LABEL}>Description</label>
        <textarea name="description" rows={4} className={INPUT} />
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <ClassField name="asset_super_class" label="Asset super-class" options={superClasses} />
        <ClassField name="asset_class" label="Asset class" options={assetClasses} />
      </div>

      <SponsorTypeahead />
      <div>
        <label className={LABEL}>…or sponsor name (free text)</label>
        <input name="sponsor_name_override" className={INPUT} />
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <div>
          <label className={LABEL}>Target raise</label>
          <input name="target_raise" placeholder="$" className={INPUT} />
        </div>
        <div>
          <label className={LABEL}>Minimum investment</label>
          <input name="minimum_investment" placeholder="$" className={INPUT} />
        </div>
        <div>
          <label className={LABEL}>Expected return %</label>
          <input name="expected_return_pct" placeholder="%" className={INPUT} />
        </div>
        <div>
          <label className={LABEL}>Term (months)</label>
          <input name="term_months" type="number" className={INPUT} />
        </div>
        <div>
          <label className={LABEL}>Deal date</label>
          <input name="deal_date" type="date" className={INPUT} />
        </div>
        <div>
          <label className={LABEL}>Close date</label>
          <input name="close_date" type="date" className={INPUT} />
        </div>
      </div>

      <div>
        <label className={LABEL}>Location</label>
        <input name="location" className={INPUT} />
      </div>

      {/* Highlights (dynamic) */}
      <div>
        <label className={LABEL}>Highlights</label>
        <div className="mt-1 space-y-2">
          {highlights.map((h, i) => (
            <div key={i} className="flex gap-2">
              <input
                value={h}
                onChange={(e) => setHighlight(i, e.target.value)}
                placeholder={`Highlight ${i + 1}`}
                className="w-full rounded-md border border-border bg-bg-card px-3 py-2 text-sm text-text-primary outline-none focus:ring-2 focus:ring-navy"
              />
              <button
                type="button"
                onClick={() => removeHighlight(i)}
                className="rounded-md border border-border px-2 text-text-muted hover:bg-border"
                aria-label="Remove highlight"
              >
                <IconX size={16} />
              </button>
            </div>
          ))}
        </div>
        <button
          type="button"
          onClick={addHighlight}
          className="mt-2 inline-flex items-center gap-1 text-sm font-medium text-navy hover:underline"
        >
          <IconPlus size={16} /> Add highlight
        </button>
      </div>

      <div>
        <label className={LABEL}>Tags (comma-separated)</label>
        <input name="tags" placeholder="real-estate, value-add" className={INPUT} />
      </div>

      <label className="flex items-center gap-2 text-sm text-text-secondary">
        <input type="checkbox" name="is_featured" /> Featured deal
      </label>

      {state?.error && <p className="text-sm text-[#9B2335]">{state.error}</p>}

      <div className="flex gap-3 border-t border-border pt-5">
        <button
          type="submit"
          name="submit_action"
          value="draft"
          disabled={pending}
          className="rounded-md border border-navy px-4 py-2 text-sm font-medium text-navy hover:bg-border disabled:opacity-60"
        >
          {pending ? "Saving…" : "Save as Draft"}
        </button>
        <button
          type="submit"
          name="submit_action"
          value="submit"
          disabled={pending}
          className="rounded-md bg-navy px-4 py-2 text-sm font-medium text-bg-app hover:opacity-90 disabled:opacity-60"
        >
          Submit for Review
        </button>
      </div>
    </form>
  );
}
