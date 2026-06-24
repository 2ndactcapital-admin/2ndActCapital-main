"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";

const INPUT =
  "rounded-md border border-border bg-bg-card px-3 py-2 text-sm text-text-primary outline-none focus:ring-2 focus:ring-navy";

export default function MarketplaceFilters({
  taxonomy = null,
  staff = false,
  stages = [],
}) {
  const router = useRouter();
  const params = useSearchParams();
  const [q, setQ] = useState(params.get("q") || "");

  const dealStage = params.get("deal_stage") || "";
  const assetClass = params.get("asset_class") || "";
  const featured = params.get("featured") === "1";
  const superClasses = taxonomy?.super_classes || [];

  function update(next) {
    const sp = new URLSearchParams(params.toString());
    for (const [key, value] of Object.entries(next)) {
      if (value) sp.set(key, value);
      else sp.delete(key);
    }
    router.push(`/marketplace?${sp.toString()}`);
  }

  function onSearchSubmit(e) {
    e.preventDefault();
    update({ q: q.trim() });
  }

  return (
    <div className="sticky top-0 z-10 -mx-1 flex flex-wrap items-center gap-3 bg-bg-app/95 px-1 py-3 backdrop-blur">
      <form onSubmit={onSearchSubmit} className="flex-1 min-w-[200px]">
        <input
          type="text"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Search deals by name…"
          className={`w-full max-w-md ${INPUT}`}
        />
      </form>

      <select
        value={assetClass}
        onChange={(e) => update({ asset_class: e.target.value })}
        className={INPUT}
        aria-label="Asset class"
      >
        <option value="">All asset classes</option>
        {superClasses.map((sc) => (
          <optgroup key={sc.key} label={sc.label}>
            {(sc.major_classes || []).map((mc) => (
              <option key={mc.key} value={mc.key}>
                {mc.label}
              </option>
            ))}
          </optgroup>
        ))}
      </select>

      {stages.length > 0 && (
        <select
          value={dealStage}
          onChange={(e) => update({ deal_stage: e.target.value })}
          className={INPUT}
          aria-label="Stage"
        >
          <option value="">All stages</option>
          {stages.map((s) => (
            <option key={s.config_key} value={s.config_key}>
              {s.config_value}
            </option>
          ))}
        </select>
      )}

      <label className="flex items-center gap-2 text-sm text-text-secondary">
        <input
          type="checkbox"
          checked={featured}
          onChange={(e) => update({ featured: e.target.checked ? "1" : "" })}
        />
        Featured only
      </label>
    </div>
  );
}
